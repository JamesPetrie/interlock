"""mac_port_shim end to end (with the sim CDC FIFO): MAC RX AXI-Stream at the
MAC clock -> CoreTSE bundle at the core clock, and back for TX. Checks data,
the no-backpressure RX drop behaviour, and that TX tvalid never drops
mid-packet."""
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

from axis_util import (AxisSource, AxisSink, TseSource, TseSink, PulseCounter,
                       rand_pkt, wait_for)

W = int(os.environ.get("W", "64"))
NB = W // 8
MAC_NS, CORE_NS = 3, 12


async def setup(dut):
    cocotb.start_soon(Clock(dut.mac_rx_clk, MAC_NS, unit="ns").start())
    cocotb.start_soon(Clock(dut.mac_tx_clk, MAC_NS, unit="ns").start())
    cocotb.start_soon(Clock(dut.core_clk, CORE_NS, unit="ns").start())
    for r in (dut.mac_rx_rst_n, dut.mac_tx_rst_n, dut.core_rst_n):
        r.value = 0
    await ClockCycles(dut.core_clk, 5)
    for r in (dut.mac_rx_rst_n, dut.mac_tx_rst_n, dut.core_rst_n):
        r.value = 1
    await ClockCycles(dut.core_clk, 5)


@cocotb.test()
async def shim_rx_path(dut):
    rng = random.Random(60)
    await setup(dut)
    src = AxisSource(dut.mac_rx_clk, dut.mac_rx_tvalid, None, dut.mac_rx_tdata, dut.mac_rx_tkeep,
                     dut.mac_rx_tlast, dut.mac_rx_tuser, nbytes=NB)
    sink = TseSink(dut.core_clk, dut.mrx_rdy, dut.mrx_acpt, dut.mrx_sof, dut.mrx_eof,
                   dut.mrx_dat, dut.mrx_bytevalid, stall=0.3)
    ovf, err = PulseCounter(dut.mac_rx_clk, dut.rx_drop_ovf), PulseCounter(dut.mac_rx_clk, dut.rx_drop_err)
    for c in (sink.run(), ovf.run(), err.run()):
        cocotb.start_soon(c)
    pkts = [rand_pkt(rng, 1, 300) for _ in range(40)]
    for i, p in enumerate(pkts):
        await src.send(p, err=(i % 9 == 4))
        if i % 9 != 4:
            await wait_for(lambda: len(sink.packets) == len([q for j, q in enumerate(pkts[:i + 1]) if j % 9 != 4]),
                           dut.core_clk, what=f"packet {i}")
    good = [p for i, p in enumerate(pkts) if i % 9 != 4]
    await ClockCycles(dut.core_clk, 50)
    assert sink.packets == good
    assert err.count == len(pkts) - len(good) and ovf.count == 0


@cocotb.test()
async def shim_rx_burst_overflow(dut):
    """Line-rate burst into a stalled core: whole packets are dropped, the
    ones that get through are intact and in order, and the path recovers."""
    rng = random.Random(61)
    await setup(dut)
    src = AxisSource(dut.mac_rx_clk, dut.mac_rx_tvalid, None, dut.mac_rx_tdata, dut.mac_rx_tkeep,
                     dut.mac_rx_tlast, dut.mac_rx_tuser, nbytes=NB)
    sink = TseSink(dut.core_clk, dut.mrx_rdy, dut.mrx_acpt, dut.mrx_sof, dut.mrx_eof,
                   dut.mrx_dat, dut.mrx_bytevalid, stall=0.0)
    ovf = PulseCounter(dut.mac_rx_clk, dut.rx_drop_ovf)
    cocotb.start_soon(sink.run())
    cocotb.start_soon(ovf.run())
    sink.enabled = False
    pkts = [rand_pkt(rng, 200, 200) for _ in range(400)]
    for p in pkts:
        await src.send(p)
    assert ovf.count > 0
    sink.enabled = True
    await ClockCycles(dut.core_clk, 60 * len(pkts))
    assert len(sink.packets) + ovf.count == len(pkts)
    it = iter(pkts)
    assert all(any(p == q for q in it) for p in sink.packets), "forwarded packets keep their order"
    tail = rand_pkt(rng, 9, 9)
    await src.send(tail)
    await wait_for(lambda: sink.packets and sink.packets[-1] == tail, dut.core_clk, what="post-burst packet")


@cocotb.test()
async def shim_tx_path(dut):
    rng = random.Random(62)
    await setup(dut)
    src = TseSource(dut.core_clk, dut.mtx_rdy, dut.mtx_acpt, dut.mtx_sof, dut.mtx_eof,
                    dut.mtx_dat, dut.mtx_bytevalid, gap=0.4)
    sink = AxisSink(dut.mac_tx_clk, dut.mac_tx_tvalid, dut.mac_tx_tready, dut.mac_tx_tdata,
                    dut.mac_tx_tkeep, dut.mac_tx_tlast, dut.mac_tx_tuser, nbytes=NB,
                    stall=0.5, check_hold=True, check_zero_pad=True)
    cocotb.start_soon(sink.run())
    pkts = [rand_pkt(rng, 1, 300) for _ in range(40)]
    for p in pkts:
        await src.send(p)
    await wait_for(lambda: len(sink.packets) == len(pkts), dut.core_clk, what="all packets")
    assert sink.packets == pkts
    assert all(u == 0 for u in sink.users)
