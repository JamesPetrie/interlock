"""End-to-end cocotb testbench for prod_ilock_top.sv — both directions plus
the certificate egress.

Drives Ethernet frames carrying canonical packets into both MAC-RX ports
(requests on the client side, responses on the server side) and checks:

  * request frames round-trip byte-exactly to the server MAC-TX;
  * the client MAC-TX carries the response frames byte-exactly, interleaved
    with certificate frames from BOTH directions (the request side's cert is
    routed across to the egress mux);
  * every certificate frame is a well-formed Ethernet frame (forced
    addresses, LENGTH = 108, valid FCS) whose record carries its direction's
    standalone batch digest d = SHA256(batch's packets) with contiguous
    signed counters (i, count), ending with certs that cover all accepted
    packets of each direction.

The batch geometry is shrunk via the parameter overrides in the Makefile;
BATCH_CYCLES below must match.
"""
import hashlib
import hmac as pyhmac
import random
import zlib

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, Combine

BATCH_CYCLES = 10000       # must match the Makefile override

MAC_CLIENT = 0x02_00_00_00_00_01
MAC_SERVER = 0x02_00_00_00_00_02

CANON_HDR_BYTES = 12
INF = 1 << 63              # canonical ID MSB: inference packet
MIN_FRAME = 64
FCS_BYTES = 4

KEY = bytes.fromhex(
    "0f0e0d0c0b0a09080706050403020100"
    "1f1e1d1c1b1a19181716151413121110"
)
EPOCH = 7
DOMAIN_SEP = 0x41545354
M_BYTES = 64                                  # i8 count4 ts8 epoch4 dir4 domain4 digest32
CERT_BYTES = CANON_HDR_BYTES + M_BYTES + 32   # reserved hdr + m + tau = 108

DIR_REQ, DIR_RSP = 0, 1


def canonical_packet(can_id: int, pkt_idx: int, pld_len: int,
                     seed: int = 0) -> bytes:
    rng = random.Random(seed)
    payload = bytes(rng.getrandbits(8) for _ in range(pld_len))
    return (can_id.to_bytes(8, "big") + pkt_idx.to_bytes(2, "big")
            + pld_len.to_bytes(2, "big") + payload)


def eth_frame(data: bytes, dst: int, src: int) -> bytes:
    """Ethernet frame: LENGTH = len(data), zero PAD to minimum, valid FCS."""
    body = (dst.to_bytes(6, "big") + src.to_bytes(6, "big")
            + len(data).to_bytes(2, "big") + data)
    if len(body) + FCS_BYTES < MIN_FRAME:
        body += bytes(MIN_FRAME - FCS_BYTES - len(body))
    return body + zlib.crc32(body).to_bytes(4, "little")


def packet_for(seq: int, pld_len: int, seed_base: int) -> bytes:
    """Sequence-valid inference canonical packet: ascending ID, pkt_idx 0."""
    return canonical_packet(INF | seq, 0, pld_len, seed=seed_base + seq)


# ------------------------------------------------------------- cert reference

def check_cert(data: bytes, pkts_by_dir, prev_i_by_dir):
    """Verify one cert record: counters contiguous per direction, digest over
    exactly the accepted packets it claims; return (dir, i)."""
    assert len(data) == CERT_BYTES, f"cert is {len(data)} bytes != {CERT_BYTES}"
    assert data[:CANON_HDR_BYTES] == bytes(CANON_HDR_BYTES), \
        f"reserved header not all-zero: {data[:CANON_HDR_BYTES].hex()}"
    m = data[CANON_HDR_BYTES:CANON_HDR_BYTES + M_BYTES]
    tau = data[CANON_HDR_BYTES + M_BYTES:]
    i = int.from_bytes(m[0:8], "big")
    count = int.from_bytes(m[8:12], "big")
    direction = int.from_bytes(m[24:28], "big")
    assert direction in pkts_by_dir, f"cert direction {direction} invalid"
    pkts, prev_i = pkts_by_dir[direction], prev_i_by_dir[direction]
    assert count >= 1 and i == prev_i + count, \
        f"dir {direction} counters not contiguous: i={i} count={count} prev_i={prev_i}"
    assert i <= len(pkts), f"dir {direction} cert i={i} out of range"
    exp_d = hashlib.sha256(b"".join(pkts[prev_i:i])).digest()
    assert m[32:64] == exp_d, f"dir {direction} digest mismatch for {prev_i}..{i}"
    assert int.from_bytes(m[20:24], "big") == EPOCH
    assert int.from_bytes(m[28:32], "big") == DOMAIN_SEP
    assert tau == pyhmac.new(KEY, m, hashlib.sha256).digest(), "cert tau mismatch"
    return direction, i


# ---------------------------------------------------------------- MAC helpers

def pack_frame(frame: bytes):
    L = len(frame)
    n = (L + 3) // 4
    words = []
    for w in range(n):
        chunk = frame[4 * w: 4 * w + 4]
        dat = 0
        for k, b in enumerate(chunk):
            dat |= b << (8 * k)
        rem = L % 4
        words.append((dat, int(w == 0), int(w == n - 1),
                      (4 - rem) if (w == n - 1 and rem) else 0))
    return words


async def drive_frames(dut, side: str, frames):
    """Drive frames into the cli_rx / srv_rx MAC bundle."""
    rdy = getattr(dut, f"{side}_rdy")
    acpt = getattr(dut, f"{side}_acpt")
    sof = getattr(dut, f"{side}_sof")
    eof = getattr(dut, f"{side}_eof")
    dat = getattr(dut, f"{side}_dat")
    bv = getattr(dut, f"{side}_bytevalid")
    for frame in frames:
        words = pack_frame(frame)
        i = 0
        while i < len(words):
            d, s, e, b = words[i]
            rdy.value = 1
            dat.value = d
            sof.value = s
            eof.value = e
            bv.value = b
            await ReadOnly()
            taken = int(acpt.value)
            await RisingEdge(dut.clk)
            if taken:
                i += 1
        rdy.value = 0
        sof.value = 0
        eof.value = 0


async def recv_frames(dut, side: str, done, throttle=0.0, rng=None,
                      max_cycles=40 * BATCH_CYCLES):
    """Collect frames from the cli_tx / srv_tx MAC bundle until done(frames)."""
    rng = rng or random.Random(0)
    rdy = getattr(dut, f"{side}_rdy")
    acpt = getattr(dut, f"{side}_acpt")
    eof = getattr(dut, f"{side}_eof")
    dat = getattr(dut, f"{side}_dat")
    bv = getattr(dut, f"{side}_bytevalid")
    frames, cur = [], bytearray()
    for _ in range(max_cycles):
        if done(frames):
            break
        acpt.value = 0 if rng.random() < throttle else 1
        await ReadOnly()
        if int(rdy.value) and int(acpt.value):
            d = int(dat.value)
            for k in range(4 - int(bv.value)):
                cur.append((d >> (8 * k)) & 0xFF)
            if int(eof.value):
                frames.append(bytes(cur))
                cur = bytearray()
        await RisingEdge(dut.clk)
    else:
        raise TimeoutError(f"{side}: {len(frames)} frames, done() never true "
                           f"after {max_cycles} cycles")
    acpt.value = 0
    return frames


def split_frame(frame: bytes, dst: int, src: int):
    """Validate framing (addresses, LENGTH, FCS) and return the DATA field."""
    assert len(frame) >= MIN_FRAME, f"runt frame ({len(frame)} bytes)"
    assert int.from_bytes(frame[0:6], "big") == dst, "DST not forced"
    assert int.from_bytes(frame[6:12], "big") == src, "SRC not forced"
    body, fcs = frame[:-FCS_BYTES], frame[-FCS_BYTES:]
    assert fcs == zlib.crc32(body).to_bytes(4, "little"), "bad FCS"
    length = int.from_bytes(frame[12:14], "big")
    assert 14 + length <= len(body), "LENGTH exceeds frame"
    return body[14:14 + length]


async def reset(dut, period_ns=8):
    cocotb.start_soon(Clock(dut.clk, period_ns, unit="ns").start())
    dut.rst_n.value = 0
    dut.key.value = int.from_bytes(KEY, "big")
    dut.epoch.value = EPOCH
    for side in ("cli_rx", "srv_rx"):
        for sig in ("rdy", "sof", "eof", "dat", "bytevalid"):
            getattr(dut, f"{side}_{sig}").value = 0
    dut.cli_tx_acpt.value = 0
    dut.srv_tx_acpt.value = 0
    for _ in range(5):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    for _ in range(2):
        await RisingEdge(dut.clk)


async def run_case(dut, req_pkts, rsp_pkts, throttle=0.0):
    """Drive both directions; verify both packet streams and all certs."""
    pkts_by_dir = {DIR_REQ: req_pkts, DIR_RSP: rsp_pkts}
    req_frames = [eth_frame(p, MAC_SERVER, MAC_CLIENT) for p in req_pkts]
    rsp_frames = [eth_frame(p, MAC_CLIENT, MAC_SERVER) for p in rsp_pkts]

    # cli_tx is done once all responses and a counter-complete cert trail per
    # direction have arrived; cert frames are recognized by canonical id 0.
    def cli_done(frames):
        n_data = 0
        last_i = {DIR_REQ: 0, DIR_RSP: 0}
        for f in frames:
            data = f[14:14 + int.from_bytes(f[12:14], "big")]
            if data[:8] == bytes(8):
                d = int.from_bytes(data[CANON_HDR_BYTES + 24:
                                        CANON_HDR_BYTES + 28], "big")
                i = int.from_bytes(data[CANON_HDR_BYTES:
                                        CANON_HDR_BYTES + 8], "big")
                last_i[d] = max(last_i.get(d, 0), i)
            else:
                n_data += 1
        return (n_data == len(rsp_pkts)
                and last_i[DIR_REQ] == len(req_pkts)
                and last_i[DIR_RSP] == len(rsp_pkts))

    tx_req = cocotb.start_soon(drive_frames(dut, "cli_rx", req_frames))
    tx_rsp = cocotb.start_soon(drive_frames(dut, "srv_rx", rsp_frames))
    rx_srv = cocotb.start_soon(recv_frames(
        dut, "srv_tx", lambda fs: len(fs) == len(req_frames),
        throttle=throttle, rng=random.Random(21)))
    rx_cli = cocotb.start_soon(recv_frames(
        dut, "cli_tx", cli_done, throttle=throttle, rng=random.Random(22)))
    await Combine(tx_req, tx_rsp, rx_srv, rx_cli)

    assert rx_srv.result() == req_frames, "request frames corrupted"

    got_rsp, last_i = [], {DIR_REQ: 0, DIR_RSP: 0}
    for f in rx_cli.result():
        data = split_frame(f, MAC_CLIENT, MAC_SERVER)
        if data[:8] == bytes(8):
            d, i = check_cert(data, pkts_by_dir, last_i)
            last_i[d] = i
        else:
            got_rsp.append(f)
    assert got_rsp == rsp_frames, "response frames corrupted"
    assert last_i == {DIR_REQ: len(req_pkts), DIR_RSP: len(rsp_pkts)}


# ---------------------------------------------------------------------- tests

@cocotb.test()
async def test_bidirectional(dut):
    """Both directions stream concurrently; packets round-trip and both
    directions attest completely on the client side."""
    await reset(dut)
    req = [packet_for(i, 30 + 11 * i, seed_base=100) for i in range(1, 7)]
    rsp = [packet_for(i, 50 + 23 * i, seed_base=200) for i in range(1, 6)]
    await run_case(dut, req, rsp)


@cocotb.test()
async def test_requests_only(dut):
    """No response traffic: the client side still carries the request
    direction's certificate (routed across to the egress mux)."""
    await reset(dut)
    req = [packet_for(i, 40 + 17 * i, seed_base=300) for i in range(1, 5)]
    await run_case(dut, req, [])


@cocotb.test()
async def test_backpressure(dut):
    """Both MAC-TX sides throttle ACPT; everything reflows without loss."""
    await reset(dut)
    req = [packet_for(i, 25 + 19 * i, seed_base=400) for i in range(1, 6)]
    rsp = [packet_for(i, 35 + 13 * i, seed_base=500) for i in range(1, 6)]
    await run_case(dut, req, rsp, throttle=0.4)
