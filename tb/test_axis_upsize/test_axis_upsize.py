"""axis_upsize: 32-bit AXI-Stream in, W-bit out; partial last beat has a
contiguous tkeep and zeroed unused lanes."""
import os
import random

import cocotb
from cocotb.clock import Clock

from axis_util import AxisSource, AxisSink, rand_pkt, reset, wait_for

W = int(os.environ.get("W", "64"))
NB = W // 8


async def setup(dut, gap, stall):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset(dut, dut.clk, dut.rst_n)
    src = AxisSource(dut.clk, dut.s_tvalid, dut.s_tready, dut.s_tdata, dut.s_tkeep,
                     dut.s_tlast, dut.s_tuser, nbytes=4, gap=gap)
    sink = AxisSink(dut.clk, dut.m_tvalid, dut.m_tready, dut.m_tdata, dut.m_tkeep,
                    dut.m_tlast, dut.m_tuser, nbytes=NB, stall=stall, check_zero_pad=True)
    cocotb.start_soon(sink.run())
    return src, sink


@cocotb.test()
async def upsize_random(dut):
    rng = random.Random(20)
    src, sink = await setup(dut, gap=0.3, stall=0.3)
    pkts = [rand_pkt(rng, 1, 3 * NB + 5) for _ in range(60)]
    for i, p in enumerate(pkts):
        await src.send(p, err=(i % 7 == 0))
    await wait_for(lambda: len(sink.packets) == len(pkts), dut.clk, what="all packets")
    assert sink.packets == pkts
    assert sink.users == [int(i % 7 == 0) for i in range(len(pkts))]


@cocotb.test()
async def upsize_streaming(dut):
    rng = random.Random(21)
    src, sink = await setup(dut, gap=0.0, stall=0.0)
    pkts = [rand_pkt(rng, 1, 6 * NB) for _ in range(40)]
    for p in pkts:
        await src.send(p)
    await wait_for(lambda: len(sink.packets) == len(pkts), dut.clk, what="all packets")
    assert sink.packets == pkts
    # every packet needs ceil(len/NB) beats, nothing more
    assert sink.beats == sum((len(p) + NB - 1) // NB for p in pkts)
