"""cocotb testbench for sha256_core.sv — one-block SHA-256 compressor.

Drives a padded 512-bit block (W[0] in the MSBs) on top of a chaining value
iv and checks digest = iv-chained compression against hashlib, both for a
single block and for multi-block messages fed block-by-block.
"""
import hashlib
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly

H_INIT = 0x6a09e667bb67ae853c6ef372a54ff53a510e527f9b05688c1f83d9ab5be0cd19


def sha_pad(msg: bytes) -> bytes:
    """SHA-256 padding: 0x80, zeros, 64-bit big-endian bit length."""
    ml = len(msg) * 8
    pad = b"\x80" + b"\x00" * ((55 - len(msg)) % 64) + ml.to_bytes(8, "big")
    return msg + pad


async def reset(dut):
    cocotb.start_soon(Clock(dut.clk, 8, unit="ns").start())
    dut.rst_n.value = 0
    dut.start.value = 0
    dut.iv.value = 0
    dut.block.value = 0
    for _ in range(5):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    for _ in range(2):
        await RisingEdge(dut.clk)


async def compress(dut, iv: int, block: bytes) -> int:
    """Run one 512-bit block; return the 256-bit digest."""
    assert len(block) == 64
    dut.iv.value = iv
    dut.block.value = int.from_bytes(block, "big")
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0
    while True:
        await ReadOnly()
        if int(dut.done.value):
            digest = int(dut.digest.value)
            await RisingEdge(dut.clk)
            return digest
        await RisingEdge(dut.clk)


async def sha256(dut, msg: bytes) -> int:
    """Full SHA-256 over msg by chaining blocks through the core."""
    padded = sha_pad(msg)
    h = H_INIT
    for off in range(0, len(padded), 64):
        h = await compress(dut, h, padded[off:off + 64])
    return h


@cocotb.test()
async def test_empty(dut):
    await reset(dut)
    got = await sha256(dut, b"")
    exp = int.from_bytes(hashlib.sha256(b"").digest(), "big")
    assert got == exp, f"{got:064x} != {exp:064x}"


@cocotb.test()
async def test_abc(dut):
    await reset(dut)
    got = await sha256(dut, b"abc")
    exp = int.from_bytes(hashlib.sha256(b"abc").digest(), "big")
    assert got == exp, f"{got:064x} != {exp:064x}"


@cocotb.test()
async def test_block_boundary(dut):
    """Lengths around the 55/56-byte single-block padding cliff and beyond."""
    await reset(dut)
    for n in [0, 1, 55, 56, 63, 64, 65, 119, 120, 200, 256]:
        msg = bytes((i * 37 + n) & 0xFF for i in range(n))
        got = await sha256(dut, msg)
        exp = int.from_bytes(hashlib.sha256(msg).digest(), "big")
        assert got == exp, f"len {n}: {got:064x} != {exp:064x}"


@cocotb.test()
async def test_random(dut):
    await reset(dut)
    rng = random.Random(1)
    for _ in range(20):
        msg = bytes(rng.randint(0, 255) for _ in range(rng.randint(0, 300)))
        got = await sha256(dut, msg)
        exp = int.from_bytes(hashlib.sha256(msg).digest(), "big")
        assert got == exp, f"{got:064x} != {exp:064x}"
