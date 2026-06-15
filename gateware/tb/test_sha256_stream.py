"""G1 — sha256_stream wrapper vs hashlib.

Feeds byte streams (with a proper valid/ready handshake), finalizes, and compares
the digest to Python hashlib. Covers empty input, padding boundaries (55/56/63/64),
the interlock's record (44B) and digest (32B) sizes, multi-block messages, gappy
feeding, and back-to-back re-init (the running-context reuse pattern)."""
import hashlib
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge


async def reset(dut):
    dut.init.value = 0
    dut.in_valid.value = 0
    dut.in_data.value = 0
    dut.fin.value = 0
    dut.reset_n.value = 0
    await ClockCycles(dut.clk, 3)
    dut.reset_n.value = 1
    await ClockCycles(dut.clk, 2)


async def send_byte(dut, b):
    """valid/ready handshake: hold data+valid, transfer on the first edge ready is high."""
    dut.in_data.value = b
    dut.in_valid.value = 1
    while True:
        await ReadOnly()                  # sample ready for the current cycle
        rdy = dut.in_ready.value
        await RisingEdge(dut.clk)          # this edge consumes the byte iff rdy was high
        if rdy:
            return


async def hash_msg(dut, msg, gappy=False):
    dut.init.value = 1
    await RisingEdge(dut.clk)
    dut.init.value = 0
    await RisingEdge(dut.clk)
    for b in msg:
        if gappy:                          # idle a cycle between bytes
            dut.in_valid.value = 0
            await RisingEdge(dut.clk)
        await send_byte(dut, b)
    dut.in_valid.value = 0
    dut.fin.value = 1
    await RisingEdge(dut.clk)
    dut.fin.value = 0
    while not dut.done.value:
        await RisingEdge(dut.clk)
    return int(dut.digest.value).to_bytes(32, "big")


@cocotb.test()
async def stream_vs_hashlib(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset(dut)

    fixed = [b"", b"abc", b"a" * 44, b"a" * 32,
             b"a" * 55, b"a" * 56, b"a" * 57, b"a" * 63, b"a" * 64, b"a" * 65,
             b"a" * 127, b"a" * 128, b"a" * 191,
             b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"]
    rng = random.Random(0xBEEF)
    fuzz = [bytes(rng.randrange(256) for _ in range(rng.randrange(0, 260)))
            for _ in range(10)]

    n = 0
    for msg in fixed + fuzz:
        got = await hash_msg(dut, msg)
        assert got == hashlib.sha256(msg).digest(), \
            f"len {len(msg)}: got {got.hex()} exp {hashlib.sha256(msg).hexdigest()}"
        n += 1

    # gappy feeding (idle cycles mid-stream) must give the same result
    for msg in [b"abc", b"a" * 70, fuzz[0]]:
        got = await hash_msg(dut, msg, gappy=True)
        assert got == hashlib.sha256(msg).digest(), f"gappy len {len(msg)}"
        n += 1

    dut._log.info(f"sha256_stream OK on {n} messages (boundaries + fuzz + gappy + reuse)")
