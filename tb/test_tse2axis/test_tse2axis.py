"""tse2axis: CoreTSE MAC-TX bundle in, 32-bit AXI-Stream out."""
import random

import cocotb
from cocotb.clock import Clock

from axis_util import TseSource, AxisSink, rand_pkt, wait_for


@cocotb.test()
async def t2a_random(dut):
    rng = random.Random(50)
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    src = TseSource(dut.clk, dut.in_rdy, dut.in_acpt, dut.in_sof, dut.in_eof, dut.in_dat,
                    dut.in_bytevalid, gap=0.3)
    sink = AxisSink(dut.clk, dut.tvalid, dut.tready, dut.tdata, dut.tkeep, dut.tlast, dut.tuser,
                    nbytes=4, stall=0.3)
    cocotb.start_soon(sink.run())
    pkts = [rand_pkt(rng, 1, 100) for _ in range(60)]
    for p in pkts:
        await src.send(p)
    await wait_for(lambda: len(sink.packets) == len(pkts), dut.clk, what="all packets")
    assert sink.packets == pkts
    assert all(u == 0 for u in sink.users)
