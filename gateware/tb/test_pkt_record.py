"""G2 — pkt_record vs wire.record / wire.packet_hash.

Streams whole packets (header+ciphertext) in, captures the emitted 44-byte record
and the parsed length/request_id/cipher_len, and compares to the Python reference
for both directions across ciphertext sizes incl. empty + padding boundaries."""
import os
import random
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "prototype"))
import wire as W

DIR = {"in": 0, "out": 1}


async def reset(dut):
    dut.dir.value = 0
    dut.in_valid.value = 0
    dut.in_data.value = 0
    dut.in_last.value = 0
    dut.reset_n.value = 0
    await ClockCycles(dut.clk, 3)
    dut.reset_n.value = 1
    await ClockCycles(dut.clk, 2)


async def drive_packet(dut, direction, pkt):
    dut.dir.value = DIR[direction]
    n = len(pkt)
    for i, b in enumerate(pkt):
        dut.in_data.value = b
        dut.in_last.value = 1 if i == n - 1 else 0
        dut.in_valid.value = 1
        while True:
            await ReadOnly()
            rdy = dut.in_ready.value
            await RisingEdge(dut.clk)
            if rdy:
                break
    dut.in_valid.value = 0
    dut.in_last.value = 0
    while not dut.rec_valid.value:
        await RisingEdge(dut.clk)
    return (int(dut.record.value).to_bytes(44, "big"),
            int(dut.length.value), int(dut.request_id.value), int(dut.cipher_len.value))


@cocotb.test()
async def record_vs_wire(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset(dut)

    rng = random.Random(0x1234)
    n = 0
    rid = 0
    for direction in ("in", "out"):
        for clen in (0, 1, 12, 31, 32, 44, 55, 63, 64, 65, 100, 200):
            rid += 1
            ct = bytes(rng.randrange(256) for _ in range(clen))
            pkt = (W.input_packet(rid, W.H(b"k%d" % rid), ct) if direction == "in"
                   else W.output_packet(rid, ct))
            rec, length, got_rid, clen_got = await drive_packet(dut, direction, pkt)
            assert rec == W.record(direction, pkt), \
                f"{direction} clen{clen}: rec {rec.hex()} != {W.record(direction, pkt).hex()}"
            assert length == clen, f"{direction} clen{clen}: length {length}"
            assert got_rid == rid, f"{direction} clen{clen}: rid {got_rid} != {rid}"
            assert clen_got == clen, f"{direction} clen{clen}: cipher_len {clen_got}"
            n += 1
    dut._log.info(f"pkt_record OK on {n} packets (both dirs, empty + boundaries)")
