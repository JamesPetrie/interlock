"""cocotb testbench for hmac_sha256.sv.

Streams a message in (same interface as sha256_msg) and checks the digest
against Python: hashlib in plain mode (hmac_en=0) and hmac in HMAC mode
(hmac_en=1), for the key compiled into the DUT.
"""
import hashlib
import hmac as pyhmac
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly

KEY = bytes.fromhex(
    "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
)


async def reset(dut):
    cocotb.start_soon(Clock(dut.clk, 8, unit="ns").start())
    dut.rst_n.value = 0
    dut.key.value = int.from_bytes(KEY, "big")
    dut.start.value = 0
    dut.hmac_en.value = 0
    dut.in_valid.value = 0
    dut.in_data.value = 0
    dut.in_bytes.value = 0
    dut.in_last.value = 0
    for _ in range(5):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    for _ in range(2):
        await RisingEdge(dut.clk)


async def run(dut, msg: bytes, hmac_en: int) -> int:
    dut.hmac_en.value = hmac_en
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
        dut.in_valid.value = 1
        dut.in_data.value = d
        dut.in_bytes.value = max(1, len(chunk))
        dut.in_last.value = int(w == nwords - 1)
        await ReadOnly()
        took = int(dut.in_ready.value)
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


@cocotb.test()
async def test_plain(dut):
    await reset(dut)
    rng = random.Random(3)
    for _ in range(12):
        msg = bytes(rng.randint(0, 255) for _ in range(rng.randint(1, 200)))
        got = await run(dut, msg, hmac_en=0)
        exp = int.from_bytes(hashlib.sha256(msg).digest(), "big")
        assert got == exp, f"plain: {got:064x} != {exp:064x}"


@cocotb.test()
async def test_hmac(dut):
    await reset(dut)
    rng = random.Random(4)
    for n in [1, 4, 31, 32, 33, 55, 56, 64, 65, 100, 200]:
        msg = bytes((i * 17 + n) & 0xFF for i in range(n))
        got = await run(dut, msg, hmac_en=1)
        exp = int.from_bytes(pyhmac.new(KEY, msg, hashlib.sha256).digest(), "big")
        assert got == exp, f"hmac len {n}: {got:064x} != {exp:064x}"


@cocotb.test()
async def test_interleaved(dut):
    """Alternate plain and HMAC back-to-back to confirm the engine resets
    cleanly between modes."""
    await reset(dut)
    rng = random.Random(8)
    for i in range(10):
        msg = bytes(rng.randint(0, 255) for _ in range(rng.randint(1, 120)))
        en = i & 1
        got = await run(dut, msg, hmac_en=en)
        if en:
            exp = pyhmac.new(KEY, msg, hashlib.sha256).digest()
        else:
            exp = hashlib.sha256(msg).digest()
        assert got == int.from_bytes(exp, "big"), f"i {i} mode {en}"
