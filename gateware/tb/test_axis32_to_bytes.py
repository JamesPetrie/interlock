"""axis32_to_bytes — 32-bit AXIS payload -> byte stream round-trip.

Packs random byte strings into AXIS beats (4 bytes/beat, AXIS-standard order,
partial last beat), drives them with backpressure on both sides, and checks the
emitted byte stream reconstructs the original."""
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge


async def reset(dut):
    dut.in_valid.value = 0
    dut.in_data.value = 0
    dut.in_keep.value = 0
    dut.in_last.value = 0
    dut.out_ready.value = 1
    dut.reset_n.value = 0
    await ClockCycles(dut.clk, 3)
    dut.reset_n.value = 1
    await ClockCycles(dut.clk, 2)


def pack(msg):
    """AXIS-standard beats: byte0 in tdata[7:0], tkeep low bits for a partial last."""
    beats = []
    for i in range(0, len(msg), 4):
        chunk = msg[i:i + 4]
        word = sum(b << (8 * j) for j, b in enumerate(chunk))
        beats.append((word, (1 << len(chunk)) - 1, i + 4 >= len(msg)))
    return beats


async def drive(dut, beats):
    for word, keep, last in beats:
        dut.in_data.value = word
        dut.in_keep.value = keep
        dut.in_last.value = 1 if last else 0
        dut.in_valid.value = 1
        while True:
            await ReadOnly()
            rdy = dut.in_ready.value
            await RisingEdge(dut.clk)
            if rdy:
                break
    dut.in_valid.value = 0


async def collect(dut, n, rng=None):
    out = bytearray()
    while len(out) < n:
        if rng is not None:
            dut.out_ready.value = 0 if rng.random() < 0.3 else 1
        await ReadOnly()
        if dut.out_valid.value and dut.out_ready.value:
            out.append(int(dut.out_data.value))
        await RisingEdge(dut.clk)
    dut.out_ready.value = 1
    return bytes(out)


@cocotb.test()
async def adapt(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset(dut)
    rng = random.Random(5)
    lengths = [1, 2, 3, 4, 5, 7, 8, 12, 44, 108, 140] + [rng.randrange(1, 300) for _ in range(10)]
    for k, L in enumerate(lengths):
        msg = bytes(rng.randrange(256) for _ in range(L))
        bp = rng if (k % 2) else None          # exercise output backpressure on half
        c = cocotb.start_soon(collect(dut, L, bp))
        await drive(dut, pack(msg))
        got = await c
        assert got == msg, f"L={L}: {got.hex()} != {msg.hex()}"
    dut._log.info(f"axis32_to_bytes OK on {len(lengths)} lengths (+ backpressure)")
