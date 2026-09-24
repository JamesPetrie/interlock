"""axis2tse: 32-bit AXI-Stream in, CoreTSE MAC-RX bundle out (SOF/EOF/
BYTEVALID); null last beats close the previous word, empty packets vanish."""
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

from axis_util import AxisSource, TseSink, rand_pkt, reset, wait_for


async def setup(dut, gap, stall):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset(dut, dut.clk, dut.rst_n)
    src = AxisSource(dut.clk, dut.tvalid, dut.tready, dut.tdata, dut.tkeep, dut.tlast,
                     None, nbytes=4, gap=gap)
    sink = TseSink(dut.clk, dut.out_rdy, dut.out_acpt, dut.out_sof, dut.out_eof,
                   dut.out_dat, dut.out_bytevalid, stall=stall)
    cocotb.start_soon(sink.run())
    return src, sink


@cocotb.test()
async def a2t_random(dut):
    rng = random.Random(40)
    src, sink = await setup(dut, gap=0.3, stall=0.3)
    pkts = [rand_pkt(rng, 1, 100) for _ in range(80)]
    for p in pkts:
        await src.send(p)
    await wait_for(lambda: len(sink.packets) == len(pkts), dut.clk, what="all packets")
    assert sink.packets == pkts


@cocotb.test()
async def a2t_streaming(dut):
    rng = random.Random(41)
    src, sink = await setup(dut, gap=0.0, stall=0.0)
    pkts = [rand_pkt(rng, 4, 64) for _ in range(40)]
    for p in pkts:
        await src.send(p)
    await wait_for(lambda: len(sink.packets) == len(pkts), dut.clk, what="all packets")
    assert sink.packets == pkts


@cocotb.test()
async def a2t_null_last_and_empty(dut):
    rng = random.Random(42)
    src, sink = await setup(dut, gap=0.2, stall=0.2)
    p8, p3, p4 = rand_pkt(rng, 8, 8), rand_pkt(rng, 3, 3), rand_pkt(rng, 4, 4)
    await src.send(p8, null_last=True)      # 2 full words + null beat -> EOF on word 2
    await src.send(b"")                     # single null beat -> nothing
    await src.send(p3)
    await src.send(b"")
    await src.send(p4, null_last=True)
    await wait_for(lambda: len(sink.packets) == 3, dut.clk, what="3 packets")
    await ClockCycles(dut.clk, 20)
    assert sink.packets == [p8, p3, p4]
