"""cocotb testbench for eth_deframe.sv.

Drives CoreTSE MAC-FIFO frames [DST|SRC|LENGTH|DATA|PAD|FCS] and checks the
module emits exactly the first LENGTH octets of DATA — word-aligned — as
AXI-Stream (tkeep, tlast), drops PAD/FCS, and flags LENGTH lies.
"""
import random
import zlib
from dataclasses import dataclass

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, Combine

ETH_HDR_BYTES = 14
FCS_BYTES = 4
MIN_FRAME = 64


def eth_fcs(body: bytes) -> bytes:
    return zlib.crc32(body).to_bytes(4, "little")


def build_frame(dst: int, src: int, data: bytes, length: int = None) -> bytes:
    """Ethernet frame: header + DATA + PAD (zeros) + FCS. `length` defaults to
    len(data) (consistent); pass another value to lie about it."""
    if length is None:
        length = len(data)
    body = dst.to_bytes(6, "big") + src.to_bytes(6, "big") + length.to_bytes(2, "big") + data
    if len(body) + FCS_BYTES < MIN_FRAME:
        body += bytes(MIN_FRAME - FCS_BYTES - len(body))
    return body + eth_fcs(body)


@dataclass
class Word:
    dat: int
    sof: int
    eof: int
    bytevalid: int


def pack_frame(payload: bytes):
    L = len(payload)
    n = (L + 3) // 4
    out = []
    for w in range(n):
        chunk = payload[4 * w: 4 * w + 4]
        dat = 0
        for k, b in enumerate(chunk):
            dat |= b << (8 * k)
        rem = L % 4
        out.append(Word(dat, int(w == 0), int(w == n - 1),
                        (4 - rem) if (w == n - 1 and rem) else 0))
    return out


async def drive_frame(dut, frame: bytes):
    words = pack_frame(frame)
    i = 0
    while i < len(words):
        w = words[i]
        dut.in_rdy.value = 1
        dut.in_dat.value = w.dat
        dut.in_sof.value = w.sof
        dut.in_eof.value = w.eof
        dut.in_bytevalid.value = w.bytevalid
        await ReadOnly()
        acpt = int(dut.in_acpt.value)
        await RisingEdge(dut.clk)
        if acpt:
            i += 1
    dut.in_rdy.value = 0
    dut.in_sof.value = 0
    dut.in_eof.value = 0


async def recv_axis(dut, n_packets, throttle=0.0, rng=None):
    rng = rng or random.Random(0)
    pkts, users, cur = [], [], bytearray()
    while len(pkts) < n_packets:
        dut.tready.value = 0 if rng.random() < throttle else 1
        await ReadOnly()
        if int(dut.tvalid.value) and int(dut.tready.value):
            d = int(dut.tdata.value)
            keep = int(dut.tkeep.value)
            last = int(dut.tlast.value)
            for k in range(4):
                if (keep >> k) & 1:
                    cur.append((d >> (8 * k)) & 0xFF)
            if last:
                pkts.append(bytes(cur))
                users.append(int(dut.tuser.value))
                cur = bytearray()
        await RisingEdge(dut.clk)
    dut.tready.value = 0
    return pkts, users


async def reset(dut, period_ns=8):
    cocotb.start_soon(Clock(dut.clk, period_ns, unit="ns").start())
    dut.rst_n.value = 0
    dut.in_rdy.value = 0
    dut.in_sof.value = 0
    dut.in_eof.value = 0
    dut.in_dat.value = 0
    dut.in_bytevalid.value = 0
    dut.tready.value = 0
    for _ in range(5):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    for _ in range(2):
        await RisingEdge(dut.clk)


async def forward(dut, frames, datas, throttle=0.0, rng=None):
    async def send():
        for f in frames:
            await drive_frame(dut, f)
    tx = cocotb.start_soon(send())
    rx = cocotb.start_soon(recv_axis(dut, len(frames), throttle=throttle, rng=rng))
    await Combine(tx, rx)
    got, users = rx.result()
    for i, (g, exp) in enumerate(zip(got, datas)):
        assert g == exp, f"packet {i}: {g[:20].hex()} != {exp[:20].hex()} (len {len(g)} vs {len(exp)})"
        assert users[i] == 0, f"packet {i}: unexpected truncation flag"


def payload(seed, n):
    rng = random.Random(seed)
    return bytes(rng.randint(0, 255) for _ in range(n))


DST, SRC = 0x02000000_0001, 0x02000000_0002


@cocotb.test()
async def test_basic(dut):
    """One padded minimum frame: only LENGTH bytes emerge; header fields parsed."""
    await reset(dut)
    data = payload(1, 18)
    await forward(dut, [build_frame(DST, SRC, data)], [data])
    await RisingEdge(dut.clk)   # EOF (PAD/FCS drain) lands after the last AXI beat
    assert int(dut.dbg_eth_dst.value) == DST
    assert int(dut.dbg_eth_src.value) == SRC
    assert int(dut.dbg_eth_len.value) == 18


@cocotb.test()
async def test_lengths(dut):
    """Every LENGTH % 4 alignment, padded and unpadded sizes."""
    await reset(dut)
    sizes = [13, 14, 15, 16, 45, 46, 47, 48, 49, 100, 1500]
    frames = [build_frame(DST, SRC, payload(s, s)) for s in sizes]
    datas = [payload(s, s) for s in sizes]
    await forward(dut, frames, datas)


@cocotb.test()
async def test_backpressure(dut):
    """AXI sink throttles; MAC side must stall without dropping bytes."""
    await reset(dut)
    sizes = [46, 100, 200, 333]
    frames = [build_frame(DST, SRC, payload(400 + s, s)) for s in sizes]
    datas = [payload(400 + s, s) for s in sizes]
    await forward(dut, frames, datas, throttle=0.4, rng=random.Random(7))


@cocotb.test()
async def test_length_lie(dut):
    """LENGTH > received DATA+PAD octets -> sticky len_err; the truncated frame
    still closes its AXI packet (tlast at EOF) and the next frame is clean."""
    await reset(dut)
    data = payload(9, 50)
    bad = build_frame(DST, SRC, data, length=1000)
    good = build_frame(DST, SRC, data)

    async def send():
        await drive_frame(dut, bad)
        await drive_frame(dut, good)

    tx = cocotb.start_soon(send())
    rx = cocotb.start_soon(recv_axis(dut, 2))
    await Combine(tx, rx)
    pkts, users = rx.result()
    assert pkts[1] == data, "frame after a truncated one not forwarded cleanly"
    assert users[0] == 1, "tuser truncation flag not set on LENGTH > received"
    assert users[1] == 0, "good frame must not be flagged"
