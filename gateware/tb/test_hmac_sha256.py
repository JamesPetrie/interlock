"""G7 — hmac_sha256 vs Python hmac (and RFC 4231 vectors).

Streams a message with a key and compares the tag to hmac.new(key, msg, sha256).
Covers RFC 4231 TC1/TC2, the interlock case (32-byte key, 108-byte body), and
fuzz over random keys (<=32 bytes) and messages."""
import hashlib
import hmac as _hmac
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge


def key_word(key: bytes) -> int:
    assert len(key) <= 32
    return int.from_bytes(key + b"\x00" * (32 - len(key)), "big")


async def reset(dut):
    dut.start.value = 0
    dut.key.value = 0
    dut.msg_valid.value = 0
    dut.msg_data.value = 0
    dut.msg_last.value = 0
    dut.reset_n.value = 0
    await ClockCycles(dut.clk, 3)
    dut.reset_n.value = 1
    await ClockCycles(dut.clk, 2)


async def hmac_of(dut, key: bytes, msg: bytes) -> bytes:
    assert len(msg) >= 1, "empty message not supported by this core"
    dut.key.value = key_word(key)
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0
    n = len(msg)
    for i, b in enumerate(msg):
        dut.msg_data.value = b
        dut.msg_last.value = 1 if i == n - 1 else 0
        dut.msg_valid.value = 1
        while True:
            await ReadOnly()
            rdy = dut.msg_ready.value
            await RisingEdge(dut.clk)
            if rdy:
                break
    dut.msg_valid.value = 0
    dut.msg_last.value = 0
    while not dut.done.value:
        await RisingEdge(dut.clk)
    return int(dut.tag.value).to_bytes(32, "big")


@cocotb.test()
async def hmac_vs_python(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset(dut)

    cases = [
        (bytes([0x0b]) * 20, b"Hi There"),                       # RFC 4231 TC1
        (b"Jefe", b"what do ya want for nothing?"),              # RFC 4231 TC2
        (hashlib.sha256(b"mac").digest(), b"x" * 108),           # interlock: 32B key, 108B body
        (b"k", b"a"),
        (bytes(range(32)), bytes(range(55))),                    # block-boundary msg
        (bytes(range(32)), bytes(range(64))),
    ]
    rng = random.Random(0x4231)
    cases += [(bytes(rng.randrange(256) for _ in range(rng.randrange(1, 32))),
               bytes(rng.randrange(256) for _ in range(rng.randrange(1, 200))))
              for _ in range(10)]

    n = 0
    for key, msg in cases:
        got = await hmac_of(dut, key, msg)
        exp = _hmac.new(key, msg, hashlib.sha256).digest()
        assert got == exp, f"key={key.hex()} msg={msg.hex()}: got {got.hex()} exp {exp.hex()}"
        n += 1
    dut._log.info(f"hmac_sha256 OK on {n} cases (RFC 4231 + interlock + fuzz)")
