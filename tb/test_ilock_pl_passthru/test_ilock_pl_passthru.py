"""ilock_pl in pass-through (TOP_KIND=2): Ethernet frames entering port 0's
MAC RX come out of port 1's MAC TX unchanged, and vice versa, across the
MRMAC pin adapters, both shims and the clock crossings. Errored frames are
dropped; frames longer than the RX FIFO are dropped whole."""
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

from axis_util import AxisSource, AxisSink, rand_pkt, wait_for

NB = 48
MAC_NS, CORE_NS = 3, 12


async def setup(dut):
    cocotb.start_soon(Clock(dut.mac_clk, MAC_NS, unit="ns").start())
    cocotb.start_soon(Clock(dut.core_clk, CORE_NS, unit="ns").start())
    dut.mac_rst_n.value = 0
    dut.core_rst_n.value = 0
    dut.pt_xover.value = 1
    dut.mode_core.value = 0
    await ClockCycles(dut.core_clk, 5)
    dut.mac_rst_n.value = 1
    dut.core_rst_n.value = 1
    await ClockCycles(dut.core_clk, 5)
    s0 = AxisSource(dut.mac_clk, dut.p0_rx_tvalid, None, dut.p0_rx_tdata, dut.p0_rx_tkeep, dut.p0_rx_tlast, dut.p0_rx_tuser, nbytes=NB)
    s1 = AxisSource(dut.mac_clk, dut.p1_rx_tvalid, None, dut.p1_rx_tdata, dut.p1_rx_tkeep, dut.p1_rx_tlast, dut.p1_rx_tuser, nbytes=NB)
    k0 = AxisSink(dut.mac_clk, dut.p0_tx_tvalid, dut.p0_tx_tready, dut.p0_tx_tdata, dut.p0_tx_tkeep, dut.p0_tx_tlast, dut.p0_tx_tuser, nbytes=NB, stall=0.3, check_hold=True)
    k1 = AxisSink(dut.mac_clk, dut.p1_tx_tvalid, dut.p1_tx_tready, dut.p1_tx_tdata, dut.p1_tx_tkeep, dut.p1_tx_tlast, dut.p1_tx_tuser, nbytes=NB, stall=0.3, check_hold=True)
    cocotb.start_soon(k0.run())
    cocotb.start_soon(k1.run())
    return s0, s1, k0, k1


@cocotb.test()
async def passthru_both_directions(dut):
    rng = random.Random(70)
    s0, s1, k0, k1 = await setup(dut)
    a = [rand_pkt(rng, 60, 1518) for _ in range(20)]     # port 0 -> port 1
    b = [rand_pkt(rng, 60, 1518) for _ in range(20)]     # port 1 -> port 0
    for i in range(20):
        await s0.send(a[i], err=(i == 7))
        await s1.send(b[i], err=(i == 3))
        await ClockCycles(dut.core_clk, 400)             # keep below the core's drain rate
    ea = [p for i, p in enumerate(a) if i != 7]
    eb = [p for i, p in enumerate(b) if i != 3]
    await wait_for(lambda: len(k1.packets) == len(ea) and len(k0.packets) == len(eb), dut.core_clk, max_cycles=100_000, what="all frames forwarded")
    assert k1.packets == ea, "port 0 -> port 1 frames differ"
    assert k0.packets == eb, "port 1 -> port 0 frames differ"
    assert int(dut.led.value) & 0b1000 == 0b1000, "drop LED should latch after the two errored frames"


@cocotb.test()
async def passthru_burst_survives(dut):
    """A line-rate burst into port 0 overflows the RX FIFO; whatever comes out
    of port 1 is intact and in order, and the path keeps working."""
    rng = random.Random(71)
    s0, s1, k0, k1 = await setup(dut)
    pkts = [rand_pkt(rng, 300, 300) for _ in range(120)]
    for p in pkts:
        await s0.send(p)
    await ClockCycles(dut.core_clk, 40 * len(pkts))
    it = iter(pkts)
    assert all(any(p == q for q in it) for p in k1.packets), "forwarded frames keep their order"
    assert 0 < len(k1.packets) < len(pkts)
    tail = rand_pkt(rng, 64, 64)
    await s0.send(tail)
    await wait_for(lambda: k1.packets and k1.packets[-1] == tail, dut.core_clk, what="post-burst frame")


@cocotb.test()
async def passthru_reflect(dut):
    """With pt_xover = 0 each port echoes its own RX to its own TX (single-fibre
    shim test on the board); the crossover path stays silent."""
    rng = random.Random(72)
    s0, s1, k0, k1 = await setup(dut)
    dut.pt_xover.value = 0
    await ClockCycles(dut.core_clk, 8)
    a = [rand_pkt(rng, 60, 1518) for _ in range(12)]
    b = [rand_pkt(rng, 60, 1518) for _ in range(12)]
    for i in range(12):
        await s0.send(a[i])
        await s1.send(b[i])
        await ClockCycles(dut.core_clk, 400)
    await wait_for(lambda: len(k0.packets) == len(a) and len(k1.packets) == len(b), dut.core_clk, max_cycles=100_000, what="all frames reflected")
    assert k0.packets == a, "port 0 did not echo its own frames"
    assert k1.packets == b, "port 1 did not echo its own frames"
    dut.pt_xover.value = 1
    dut.mode_core.value = 0
    await ClockCycles(dut.core_clk, 8)
    c = rand_pkt(rng, 100, 100)
    await s0.send(c)
    await wait_for(lambda: k1.packets and k1.packets[-1] == c, dut.core_clk, what="crossover again after the switch")
