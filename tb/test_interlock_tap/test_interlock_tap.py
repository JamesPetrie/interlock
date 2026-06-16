"""interlock_tap conformance vs the Python golden (prototype/interlock.py).

Drives wire.py packets as AXIS-32 beats into the inline tap (the deframe side),
captures the certificate it emits, and asserts it equals the model's on_second()
byte-for-byte. One cert equality covers the whole new integration chain:
axis32_to_bytes byte order -> interlock_core (record/fold/cert/HMAC) -> capture.

The tap hardcodes nonce = H("nonce")[:16] and mac = H("mac") (pulsed once at
reset), so the golden must use the same — see boot().
"""
import os
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge

sys.path.insert(0, "/root/fpga/interlock/prototype")
import wire as W
from interlock import Interlock

IID, N = 7, 8
SMAX, CAP = 100000, 100000
MAC = W.H(b"mac")
NONCE = W.H(b"nonce")[:16]


async def reset(dut):
    dut.s_tvalid.value = 0
    dut.s_tdata.value = 0
    dut.s_tkeep.value = 0
    dut.s_tlast.value = 0
    dut.s_dir.value = 0
    dut.m_tready.value = 1          # downstream (reframe) always accepts
    dut.cert_ready.value = 1        # cert sink always ready
    dut.bucket_tick.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 3)


async def wait_idle(dut):
    while not dut.dbg_idle.value:
        await RisingEdge(dut.clk)


async def send_packet(dut, direction, pkt):
    """Drive one wire.py packet as AXIS-32 beats (tdata[7:0] = first byte,
    contiguous tkeep), then wait for the core to finish it (pkt_done count++)."""
    before = int(dut.dbg_pkt_done.value)
    dut.s_dir.value = 0 if direction == "in" else 1
    nwords = (len(pkt) + 3) // 4
    for wi in range(nwords):
        chunk = pkt[4 * wi: 4 * wi + 4]
        dut.s_tdata.value = int.from_bytes(chunk + b"\x00" * (4 - len(chunk)), "little")
        dut.s_tkeep.value = (1 << len(chunk)) - 1
        dut.s_tlast.value = 1 if wi == nwords - 1 else 0
        dut.s_tvalid.value = 1
        while True:
            await ReadOnly()
            rdy = dut.s_tready.value
            await RisingEdge(dut.clk)
            if rdy:
                break
    dut.s_tvalid.value = 0
    dut.s_tlast.value = 0
    while int(dut.dbg_pkt_done.value) == before:
        await RisingEdge(dut.clk)


async def tick(dut):
    await wait_idle(dut)
    dut.bucket_tick.value = 1
    await RisingEdge(dut.clk)
    dut.bucket_tick.value = 0
    while dut.dbg_idle.value:          # boundary starts
        await RisingEdge(dut.clk)
    while not dut.dbg_idle.value:      # ...and completes (cert emits on the Nth)
        await RisingEdge(dut.clk)


def cert_collector(dut, box):
    async def run():
        buf = bytearray()
        while True:
            await ReadOnly()
            if dut.cert_valid.value:          # cert_ready tied high inside the tap
                buf.append(int(dut.cert_data.value))
                if bool(dut.cert_last.value):
                    box["cert"] = bytes(buf)
                    return
            await RisingEdge(dut.clk)
    return cocotb.start_soon(run())


async def run_window(dut, ref, schedule):
    box = {}
    cert_collector(dut, box)
    for i in range(N):
        await wait_idle(dut)
        for direction, pkt in schedule.get(i, []):
            await send_packet(dut, direction, pkt)
            ref.on_packet(direction, pkt)
        await tick(dut)
        ref.on_bucket_boundary()
    while "cert" not in box:
        await RisingEdge(dut.clk)
    return box["cert"], ref.on_second()


def pair(rid, prompt, key, resp):
    return (W.input_packet(rid, key, W.encrypt(key, b"in", W.tokens_to_bytes(prompt))),
            W.output_packet(rid, W.encrypt(key, b"out", W.tokens_to_bytes(resp))))


async def boot(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset(dut)
    ref = Interlock(MAC, IID, s_max=SMAX, capacity=CAP, buckets_per_cert=N)
    ref.on_nonce(NONCE)               # tap latches the same fixed nonce internally
    return ref


@cocotb.test()
async def honest_pair(dut):
    ref = await boot(dut)
    in_pkt, out_pkt = pair(1, [10, 20, 30], W.H(b"k"), [3681, 338, 278])
    cert, ref_cert = await run_window(dut, ref, {0: [("in", in_pkt)], 1: [("out", out_pkt)]})
    assert cert == ref_cert, f"\n dut={cert.hex()}\n ref={ref_cert.hex()}"
    dut._log.info("honest_pair: tap cert matches golden (%d bytes)" % len(cert))


@cocotb.test()
async def out_only(dut):
    """Response-path core (s_dir=1): out-only packets vs a golden fed only out.
    This is exactly the rsp core's behaviour in the two-core design — its cert
    carries overall_out; overall_in stays empty."""
    ref = await boot(dut)
    key = W.H(b"k")
    out1 = W.output_packet(1, W.encrypt(key, b"out", W.tokens_to_bytes([5, 6])))
    out2 = W.output_packet(2, W.encrypt(key, b"out", W.tokens_to_bytes([7])))
    cert, ref_cert = await run_window(dut, ref, {0: [("out", out1)], 3: [("out", out2)]})
    assert cert == ref_cert, f"\n dut={cert.hex()}\n ref={ref_cert.hex()}"
    dut._log.info("out_only: rsp-direction cert matches golden")


@cocotb.test()
async def multi_turn(dut):
    ref = await boot(dut)
    for rid in (1, 2, 3):
        in_pkt, out_pkt = pair(rid, [rid, rid + 1], W.H(b"k%d" % rid), [rid * 7, rid * 9])
        cert, ref_cert = await run_window(dut, ref, {0: [("in", in_pkt)], 1: [("out", out_pkt)]})
        assert cert == ref_cert, f"window {rid} mismatch:\n dut={cert.hex()}\n ref={ref_cert.hex()}"
    dut._log.info("multi_turn: 3 windows, tap certs match golden")
