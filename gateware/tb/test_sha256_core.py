"""G0 — vendored secworks sha256_core vs NIST vectors + fuzz against hashlib.

Drives raw padded 512-bit blocks (init on the first, next on the rest) and reads
back the digest, comparing to Python hashlib. The handshake follows the secworks
core testbench (assert init/next one cycle, deassert, poll `ready`)."""
import hashlib
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge


def sha256_blocks(msg: bytes):
    """Standard SHA-256 padding -> list of 64-byte blocks (the G1 RTL reference)."""
    ml = len(msg) * 8
    p = msg + b"\x80"
    while len(p) % 64 != 56:
        p += b"\x00"
    p += ml.to_bytes(8, "big")
    return [p[i:i + 64] for i in range(0, len(p), 64)]


async def reset(dut):
    dut.init.value = 0
    dut.next.value = 0
    dut.mode.value = 1            # 1 = SHA-256
    dut.block.value = 0
    dut.reset_n.value = 0
    await ClockCycles(dut.clk, 3)
    dut.reset_n.value = 1
    await ClockCycles(dut.clk, 2)
    while not dut.ready.value:
        await RisingEdge(dut.clk)


async def wait_ready(dut):
    """`ready` is combinational (state==IDLE): observe the busy phase, then done."""
    while dut.ready.value:        # leave IDLE
        await RisingEdge(dut.clk)
    while not dut.ready.value:    # 66-cycle compression, back to IDLE
        await RisingEdge(dut.clk)


async def run_block(dut, block, first):
    dut.block.value = int.from_bytes(block, "big")
    dut.init.value = 1 if first else 0
    dut.next.value = 0 if first else 1
    await RisingEdge(dut.clk)     # core samples init/next here
    dut.init.value = 0
    dut.next.value = 0
    await wait_ready(dut)


async def digest_of(dut, msg):
    for i, b in enumerate(sha256_blocks(msg)):
        await run_block(dut, b, i == 0)
    return int(dut.digest.value).to_bytes(32, "big")


@cocotb.test()
async def nist_and_fuzz(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset(dut)

    # NIST FIPS 180-4 examples + padding-boundary lengths + fuzz
    cases = [
        b"",
        b"abc",
        b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq",  # 2 blocks
        b"a" * 55, b"a" * 56, b"a" * 63, b"a" * 64, b"a" * 65, b"a" * 120,
    ]
    rng = random.Random(0xC0FFEE)
    cases += [bytes(rng.randrange(256) for _ in range(rng.randrange(0, 200)))
              for _ in range(12)]

    for msg in cases:
        got = await digest_of(dut, msg)
        exp = hashlib.sha256(msg).digest()
        assert got == exp, f"len {len(msg)}: got {got.hex()} exp {exp.hex()}"
    dut._log.info(f"sha256_core OK on {len(cases)} messages (NIST + fuzz)")
