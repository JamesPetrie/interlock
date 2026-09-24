"""axis_pkt_fifo: store-and-forward, drops errored packets and packets that
do not fit, never corrupts the ones it forwards."""
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

from axis_util import AxisSource, AxisSink, PulseCounter, rand_pkt, reset, wait_for

W = int(os.environ.get("W", "64"))
NB = W // 8
DEPTH = int(os.environ.get("DEPTH", "64"))


async def setup(dut, stall):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset(dut, dut.clk, dut.rst_n)
    src = AxisSource(dut.clk, dut.s_tvalid, None, dut.s_tdata, dut.s_tkeep,
                     dut.s_tlast, dut.s_tuser, nbytes=NB, gap=0.0)
    sink = AxisSink(dut.clk, dut.m_tvalid, dut.m_tready, dut.m_tdata, dut.m_tkeep,
                    dut.m_tlast, dut.m_tuser, nbytes=NB, stall=stall)
    ovf, err = PulseCounter(dut.clk, dut.drop_ovf), PulseCounter(dut.clk, dut.drop_err)
    cocotb.start_soon(sink.run())
    cocotb.start_soon(ovf.run())
    cocotb.start_soon(err.run())
    return src, sink, ovf, err


def is_subsequence(sub, seq):
    it = iter(seq)
    return all(any(x == y for y in it) for x in sub)


@cocotb.test()
async def fifo_passthrough(dut):
    rng = random.Random(30)
    src, sink, ovf, err = await setup(dut, stall=0.3)
    pkts = [rand_pkt(rng, 1, 4 * NB) for _ in range(40)]
    for p in pkts:
        await src.send(p)
        await ClockCycles(dut.clk, 12)          # keep the average rate below the sink's
    await wait_for(lambda: len(sink.packets) == len(pkts), dut.clk, what="all packets")
    assert sink.packets == pkts
    assert ovf.count == 0 and err.count == 0


@cocotb.test()
async def fifo_drops_errored(dut):
    rng = random.Random(31)
    src, sink, ovf, err = await setup(dut, stall=0.2)
    pkts = [rand_pkt(rng, 1, 4 * NB) for _ in range(30)]
    bad = {i for i in range(len(pkts)) if i % 4 == 1}
    for i, p in enumerate(pkts):
        await src.send(p, err=(i in bad))
        await ClockCycles(dut.clk, 10)
    good = [p for i, p in enumerate(pkts) if i not in bad]
    await wait_for(lambda: len(sink.packets) == len(good), dut.clk, what="good packets")
    await ClockCycles(dut.clk, 50)
    assert sink.packets == good
    assert err.count == len(bad) and ovf.count == 0


@cocotb.test()
async def fifo_overflow(dut):
    rng = random.Random(32)
    src, sink, ovf, err = await setup(dut, stall=0.0)
    sink.enabled = False                          # hold tready low
    pkts = [rand_pkt(rng, 10 * NB, 10 * NB) for _ in range(3 * DEPTH // 10 + 4)]
    for p in pkts:
        await src.send(p)
    await ClockCycles(dut.clk, 20)
    assert ovf.count >= 1, "expected overflow drops"
    sink.enabled = True
    await ClockCycles(dut.clk, 20 * len(pkts) + 200)
    assert len(sink.packets) + ovf.count == len(pkts), "each packet is forwarded or dropped exactly once"
    assert is_subsequence(sink.packets, pkts), "forwarded packets keep their order"
    assert all(len(p) == 10 * NB for p in sink.packets), "forwarded packets are intact"
    # the FIFO recovers: a packet sent afterwards comes through
    tail = rand_pkt(rng, 3, 3)
    await src.send(tail)
    await wait_for(lambda: sink.packets and sink.packets[-1] == tail, dut.clk, what="post-overflow packet")


@cocotb.test()
async def fifo_drops_oversize(dut):
    rng = random.Random(33)
    src, sink, ovf, err = await setup(dut, stall=0.0)
    big = rand_pkt(rng, (DEPTH + 5) * NB, (DEPTH + 5) * NB)
    small = rand_pkt(rng, 5, 5)
    await src.send(big)
    await src.send(small)
    await wait_for(lambda: len(sink.packets) == 1, dut.clk, what="the small packet")
    await ClockCycles(dut.clk, 50)
    assert sink.packets == [small]
    assert ovf.count == 1 and err.count == 0
