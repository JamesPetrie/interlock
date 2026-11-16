"""cocotb testbench for sha256_msg.sv — streaming SHA-256 with padding.

Streams a byte message in (1..4 valid bytes per word, in_last on the final
word) and checks the digest against hashlib across lengths that exercise the
single-block / two-block padding split and partial final words.
"""
import hashlib
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly


async def reset(dut):
    cocotb.start_soon(Clock(dut.clk, 8, unit="ns").start())
    dut.rst_n.value = 0
    dut.start.value = 0
    dut.in_valid.value = 0
    dut.in_data.value = 0
    dut.in_bytes.value = 0
    dut.in_last.value = 0
    for _ in range(5):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    for _ in range(2):
        await RisingEdge(dut.clk)


async def hash_msg(dut, msg: bytes, throttle=0.0, rng=None) -> int:
    if rng is None:
        rng = random.Random(0)
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0

    nwords = max(1, (len(msg) + 3) // 4)
    w = 0
    while w < nwords:
        chunk = msg[4 * w: 4 * w + 4]
        d = 0
        for k, b in enumerate(chunk):
            d |= b << (8 * k)
        gate = rng.random() >= throttle
        dut.in_valid.value = int(gate)
        dut.in_data.value = d
        dut.in_bytes.value = max(1, len(chunk))
        dut.in_last.value = int(w == nwords - 1)
        await ReadOnly()
        took = gate and int(dut.in_ready.value)
        await RisingEdge(dut.clk)
        if took:
            w += 1
    dut.in_valid.value = 0
    dut.in_last.value = 0

    while True:
        await ReadOnly()
        if int(dut.done.value):
            digest = int(dut.digest.value)
            await RisingEdge(dut.clk)
            return digest
        await RisingEdge(dut.clk)


async def hash_chunked(dut, msg: bytes, sizes) -> int:
    """Feed msg as a sequence of 1..4-byte chunks (partial words mid-stream)."""
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0

    off, n = 0, len(sizes)
    j = 0
    while j < n:
        chunk = msg[off: off + sizes[j]]
        d = 0
        for k, b in enumerate(chunk):
            d |= b << (8 * k)
        dut.in_valid.value = 1
        dut.in_data.value = d
        dut.in_bytes.value = len(chunk)
        dut.in_last.value = int(j == n - 1)
        await ReadOnly()
        took = int(dut.in_ready.value)
        await RisingEdge(dut.clk)
        if took:
            off += sizes[j]
            j += 1
    dut.in_valid.value = 0
    dut.in_last.value = 0

    while True:
        await ReadOnly()
        if int(dut.done.value):
            digest = int(dut.digest.value)
            await RisingEdge(dut.clk)
            return digest
        await RisingEdge(dut.clk)


def rand_sizes(total, rng):
    sizes = []
    left = total
    while left:
        s = min(left, rng.randint(1, 4))
        sizes.append(s)
        left -= s
    return sizes


@cocotb.test()
async def test_straddle(dut):
    """Partial words mid-stream that push a later word across the 64-byte block
    boundary — the case a flat per-word feed never hits."""
    await reset(dut)
    # land boff on 61/62/63 then feed a 2..4-byte word that straddles
    for pre, sizes in [
        (61, [4]), (62, [3]), (62, [4]), (63, [2]), (63, [4]),
        (125, [4]), (126, [3]),                  # straddle the second block too
    ]:
        total = pre + sizes[0]
        msg = bytes((7 * i + total) & 0xFF for i in range(total))
        feed = [4] * (pre // 4) + ([pre % 4] if pre % 4 else []) + sizes
        got = await hash_chunked(dut, msg, feed)
        exp = int.from_bytes(hashlib.sha256(msg).digest(), "big")
        assert got == exp, f"pre {pre} sizes {sizes}: {got:064x} != {exp:064x}"


@cocotb.test()
async def test_chunked_random(dut):
    """Random 1..4-byte chunking over many lengths vs hashlib."""
    await reset(dut)
    rng = random.Random(17)
    for _ in range(40):
        n = rng.randint(1, 400)
        msg = bytes(rng.randint(0, 255) for _ in range(n))
        got = await hash_chunked(dut, msg, rand_sizes(n, rng))
        exp = int.from_bytes(hashlib.sha256(msg).digest(), "big")
        assert got == exp, f"len {n}: {got:064x} != {exp:064x}"


@cocotb.test()
async def test_lengths(dut):
    """Every length across the padding cliffs and a couple of blocks."""
    await reset(dut)
    for n in [1, 2, 3, 4, 5, 31, 32, 55, 56, 60, 63, 64, 65, 95, 96, 120, 200]:
        msg = bytes((i * 31 + n) & 0xFF for i in range(n))
        got = await hash_msg(dut, msg)
        exp = int.from_bytes(hashlib.sha256(msg).digest(), "big")
        assert got == exp, f"len {n}: {got:064x} != {exp:064x}"


@cocotb.test()
async def test_back_to_back(dut):
    """Consecutive messages without re-reset reuse the engine cleanly."""
    await reset(dut)
    rng = random.Random(5)
    for _ in range(15):
        msg = bytes(rng.randint(0, 255) for _ in range(rng.randint(1, 250)))
        got = await hash_msg(dut, msg)
        exp = int.from_bytes(hashlib.sha256(msg).digest(), "big")
        assert got == exp, f"{got:064x} != {exp:064x}"


@cocotb.test()
async def test_throttled(dut):
    """Absorb side stalls (in_valid gaps) must not change the result."""
    await reset(dut)
    rng = random.Random(9)
    for _ in range(8):
        msg = bytes(rng.randint(0, 255) for _ in range(rng.randint(1, 200)))
        got = await hash_msg(dut, msg, throttle=0.5, rng=rng)
        exp = int.from_bytes(hashlib.sha256(msg).digest(), "big")
        assert got == exp, f"{got:064x} != {exp:064x}"
