"""axis_downsize: W-bit AXI-Stream in, 32-bit out, same bytes, tlast/tkeep on
the right beat, tuser (error at tlast) carried, null last beat passed on."""
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
                     dut.s_tlast, dut.s_tuser, nbytes=NB, gap=gap)
    sink = AxisSink(dut.clk, dut.m_tvalid, dut.m_tready, dut.m_tdata, dut.m_tkeep,
                    dut.m_tlast, dut.m_tuser, nbytes=4, stall=stall)
    cocotb.start_soon(sink.run())
    return src, sink


@cocotb.test()
async def downsize_random(dut):
    rng = random.Random(10)
    src, sink = await setup(dut, gap=0.3, stall=0.3)
    pkts = [rand_pkt(rng, 1, 3 * NB + 5) for _ in range(60)]
    for i, p in enumerate(pkts):
        await src.send(p, err=(i % 5 == 0))
    await wait_for(lambda: len(sink.packets) == len(pkts), dut.clk, what="all packets")
    assert sink.packets == pkts
    assert sink.users == [int(i % 5 == 0) for i in range(len(pkts))]


@cocotb.test()
async def downsize_streaming(dut):
    """No gaps, no stalls: back-to-back beats must not lose anything."""
    rng = random.Random(11)
    src, sink = await setup(dut, gap=0.0, stall=0.0)
    pkts = [rand_pkt(rng, NB, 6 * NB) for _ in range(30)]
    for p in pkts:
        await src.send(p)
    await wait_for(lambda: len(sink.packets) == len(pkts), dut.clk, what="all packets")
    assert sink.packets == pkts


@cocotb.test()
async def downsize_null_last(dut):
    rng = random.Random(12)
    src, sink = await setup(dut, gap=0.2, stall=0.2)
    pkts = [rand_pkt(rng, 2 * NB, 2 * NB), rand_pkt(rng, 7, 7), rand_pkt(rng, NB, NB)]
    await src.send(pkts[0], null_last=True)
    await src.send(pkts[1])
    await src.send(pkts[2], null_last=True)
    await wait_for(lambda: len(sink.packets) == 3, dut.clk, what="3 packets")
    assert sink.packets == pkts
    assert sink.null_last_beats == 2, "null last beats must pass through as one null 32-bit beat"
