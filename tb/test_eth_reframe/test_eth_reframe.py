"""cocotb testbench for eth_reframe.sv.

Drives canonical packets as AXI-Stream (tuser = byte length on SOP) and checks
the emitted MAC frame: forced DST/SRC, regenerated LENGTH, zero PAD to the
64-byte minimum, valid trailing FCS — i.e. output == golden frame.
"""
import random
import zlib

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, Combine

FORCE_DST = 0x02_00_00_00_00_02
FORCE_SRC = 0x02_00_00_00_00_01
MIN_FRAME = 64
FCS_BYTES = 4


def golden_frame(data: bytes) -> bytes:
    body = (FORCE_DST.to_bytes(6, "big") + FORCE_SRC.to_bytes(6, "big")
            + len(data).to_bytes(2, "big") + data)
    if len(body) + FCS_BYTES < MIN_FRAME:
        body += bytes(MIN_FRAME - FCS_BYTES - len(body))
    return body + zlib.crc32(body).to_bytes(4, "little")


def pack_axis(data: bytes):
    n = (len(data) + 3) // 4
    out = []
    for w in range(n):
        chunk = data[4 * w: 4 * w + 4]
        d = keep = 0
        for k, b in enumerate(chunk):
            d |= b << (8 * k)
            keep |= 1 << k
        out.append((d, keep, int(w == n - 1)))
    return out


async def drive_packet(dut, data: bytes):
    beats = pack_axis(data)
    i = 0
    while i < len(beats):
        d, keep, last = beats[i]
        dut.tvalid.value = 1
        dut.tdata.value = d
        dut.tkeep.value = keep
        dut.tlast.value = last
        dut.tuser.value = len(data)
        await ReadOnly()
        ready = int(dut.tready.value)
        await RisingEdge(dut.clk)
        if ready:
            i += 1
    dut.tvalid.value = 0
    dut.tlast.value = 0


async def recv_frames(dut, n_frames, throttle=0.0, rng=None):
    rng = rng or random.Random(0)
    frames, cur = [], bytearray()
    while len(frames) < n_frames:
        dut.out_acpt.value = 0 if rng.random() < throttle else 1
        await ReadOnly()
        if int(dut.out_rdy.value) and int(dut.out_acpt.value):
            dat = int(dut.out_dat.value)
            bv = int(dut.out_bytevalid.value)
            eof = int(dut.out_eof.value)
            for k in range(4 - bv):
                cur.append((dat >> (8 * k)) & 0xFF)
            if eof:
                frames.append(bytes(cur))
                cur = bytearray()
        await RisingEdge(dut.clk)
    dut.out_acpt.value = 0
    return frames


async def reset(dut, period_ns=8):
    cocotb.start_soon(Clock(dut.clk, period_ns, unit="ns").start())
    dut.rst_n.value = 0
    dut.tvalid.value = 0
    dut.tdata.value = 0
    dut.tkeep.value = 0
    dut.tlast.value = 0
    dut.tuser.value = 0
    dut.out_acpt.value = 0
    for _ in range(5):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    for _ in range(2):
        await RisingEdge(dut.clk)


async def forward(dut, datas, throttle=0.0, rng=None):
    async def send():
        for d in datas:
            await drive_packet(dut, d)
    tx = cocotb.start_soon(send())
    rx = cocotb.start_soon(recv_frames(dut, len(datas), throttle=throttle, rng=rng))
    await Combine(tx, rx)
    got = rx.result()
    for i, (g, d) in enumerate(zip(got, datas)):
        exp = golden_frame(d)
        assert g == exp, (f"frame {i} (data {len(d)}B): {g[:24].hex()} != {exp[:24].hex()}"
                          f" (len {len(g)} vs {len(exp)})")


def payload(seed, n):
    rng = random.Random(seed)
    return bytes(rng.getrandbits(8) for _ in range(n))


@cocotb.test()
async def test_no_pad(dut):
    """46-byte canonical packet: exact minimum frame without padding."""
    await reset(dut)
    await forward(dut, [payload(1, 46)])


@cocotb.test()
async def test_padded(dut):
    """Short canonical packets are zero-padded to the 64-byte minimum frame."""
    await reset(dut)
    await forward(dut, [payload(2, 13), payload(3, 20), payload(4, 45)])


@cocotb.test()
async def test_lengths(dut):
    """Every payload % 4 alignment across sizes."""
    await reset(dut)
    await forward(dut, [payload(s, s) for s in [13, 46, 47, 48, 49, 100, 333, 1500]])


@cocotb.test()
async def test_backpressure(dut):
    """MAC sink throttles ACPT; AXI-Stream stalls without losing bytes."""
    await reset(dut)
    await forward(dut, [payload(s, s) for s in [46, 100, 333]],
                  throttle=0.4, rng=random.Random(9))
