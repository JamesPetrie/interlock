"""Host-style folding through the FULL chain: an ethernet frame carrying a wire
packet -> eth_deframe -> interlock_tap -> interlock_core -> cert.

This is the exact path the host drives on silicon, which the cert-egress test only
ever checked for *egress*, never for *folding*. Two checks:
  fold_input_at_bucket0  : a correctly-bucketed (bucket 0) input frame FOLDS ->
                           the bucket-0 cert's overall_in is non-empty and
                           byte-matches the Python golden.
  drop_wrong_bucket_frame: an input frame declaring the wrong bucket is DROPPED ->
                           the bucket-0 cert's overall_in stays all-empty.

Topology: tse0_mrx -> deframe_req -> tap_req(s_dir=0, folds INPUT). Certs egress
tse0_mtx. TICK_DIV=4000 (Makefile) so the frame folds into bucket 0 before tick 1.
"""
import os
import sys
import zlib

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "prototype"))
import wire as W
from interlock import Interlock

IID, N = 7, 8
SMAX, CAP = 100000, 100000
MAC = W.H(b"mac")
NONCE = W.H(b"nonce")[:16]
CERT_DST = bytes.fromhex("0200000000ce")
EMPTY = W.overall_hash([W.bucket_hash([])] * N)     # overall of an all-empty window
TICK_DIV = 4000


def eth_frame(dst, src, data):
    body = dst + src + len(data).to_bytes(2, "big") + data
    if len(body) + 4 < 64:
        body += bytes(64 - 4 - len(body))
    return body + zlib.crc32(body).to_bytes(4, "little")


async def reset(dut):
    cocotb.start_soon(Clock(dut.clk, 8, unit="ns").start())
    for p in ("tse0_mrx_", "tse1_mrx_"):
        getattr(dut, p + "rdy").value = 0
        getattr(dut, p + "sof").value = 0
        getattr(dut, p + "eof").value = 0
        getattr(dut, p + "dat").value = 0
        getattr(dut, p + "bytevalid").value = 0
    dut.tse0_mtx_acpt.value = 1
    dut.tse1_mtx_acpt.value = 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 6)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 3)


async def drive_rx(dut, pfx, frame):
    L = len(frame); nw = (L + 3) // 4; i = 0
    while i < nw:
        chunk = frame[4 * i:4 * i + 4]
        dat = 0
        for k, b in enumerate(chunk):
            dat |= b << (8 * k)
        rem = L % 4
        bv = (4 - rem) if (i == nw - 1 and rem) else 0
        getattr(dut, pfx + "rdy").value = 1
        getattr(dut, pfx + "dat").value = dat
        getattr(dut, pfx + "sof").value = 1 if i == 0 else 0
        getattr(dut, pfx + "eof").value = 1 if i == nw - 1 else 0
        getattr(dut, pfx + "bytevalid").value = bv
        await ReadOnly()
        acc = int(getattr(dut, pfx + "acpt").value)
        await RisingEdge(dut.clk)
        if acc:
            i += 1
    getattr(dut, pfx + "rdy").value = 0
    getattr(dut, pfx + "sof").value = 0
    getattr(dut, pfx + "eof").value = 0


async def capture_bucket0_certs(dut, cycles):
    """Collect cert frames (DST CE) on tse0_mtx, return list of (overall_in,
    overall_out) for the certs whose bucket_start == 0."""
    out, cur = [], bytearray()
    for _ in range(cycles):
        dut.tse0_mtx_acpt.value = 1
        await ReadOnly()
        rdy = int(dut.tse0_mtx_rdy.value)
        eof = int(dut.tse0_mtx_eof.value)
        dat = int(dut.tse0_mtx_dat.value)
        bv = int(dut.tse0_mtx_bytevalid.value)
        await RisingEdge(dut.clk)
        if rdy:
            for k in range(4 - bv):
                cur.append((dat >> (8 * k)) & 0xFF)
            if eof:
                f, cur = bytes(cur), bytearray()
                if f[0:6] == CERT_DST and f[14:22] == b"ilock-v5":
                    if int.from_bytes(f[30:38], "big") == 0:      # bucket_start == 0
                        out.append((f[42:74], f[74:106]))          # overall_in, overall_out
    return out


def golden_overall_in(in_pkt):
    ref = Interlock(MAC, IID, s_max=SMAX, capacity=CAP, buckets_per_cert=N)
    ref.on_nonce(NONCE)
    ref.on_packet("in", in_pkt)                  # folds into bucket 0
    for _ in range(N):
        ref.on_bucket_boundary()
    return W.parse_certificate(ref.on_second())["overall_in"]


def mk_input(rid, bucket):
    key = W.H(b"k")
    return W.input_packet(rid, bucket, key, W.encrypt(key, b"in", W.tokens_to_bytes([10, 20, 30])))


@cocotb.test()
async def fold_input_at_bucket0(dut):
    await reset(dut)
    in_pkt = mk_input(1, 0)
    await drive_rx(dut, "tse0_mrx_",
                   eth_frame(b"\xde\xad\xbe\xef\x00\x01", b"\xde\xad\xbe\xef\x00\x02", in_pkt))
    certs = await capture_bucket0_certs(dut, N * TICK_DIV + 6000)
    assert certs, "no bucket_start=0 cert egressed"
    folded = [oi for oi, oo in certs if oi != EMPTY]
    assert folded, "input did NOT fold through the frame path (overall_in all-empty)"
    assert folded[0] == golden_overall_in(in_pkt), \
        "overall_in mismatch:\n dut=%s\n gold=%s" % (folded[0].hex(), golden_overall_in(in_pkt).hex())
    dut._log.info("fold_input_at_bucket0: frame -> deframe -> tap -> core folded; overall_in byte-matches golden")


@cocotb.test()
async def drop_wrong_bucket_frame(dut):
    await reset(dut)
    in_pkt = mk_input(1, 5)            # declares bucket 5, core is at 0 -> must drop
    await drive_rx(dut, "tse0_mrx_",
                   eth_frame(b"\xde\xad\xbe\xef\x00\x01", b"\xde\xad\xbe\xef\x00\x02", in_pkt))
    certs = await capture_bucket0_certs(dut, N * TICK_DIV + 6000)
    assert certs, "no bucket_start=0 cert egressed"
    assert all(oi == EMPTY for oi, oo in certs), \
        "wrong-bucket frame was NOT dropped (some bucket-0 cert has non-empty overall_in)"
    dut._log.info("drop_wrong_bucket_frame: wrong-bucket frame dropped through the full chain (overall_in empty)")
