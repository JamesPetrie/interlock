"""dbg_telemetry — counters, sticky flags, and runtime probe-mux selection.

Models the read-only debug block: pulse events and check counters; pulse flags,
read sticky, write-1-to-clear; drive distinct probe lanes and confirm probe_sel
selects the right one at runtime (the no-rebuild observability path)."""
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge

NCTR, NPROBE = 8, 8


async def reset(dut):
    dut.evt.value = 0
    dut.flag_set.value = 0
    dut.probe_in.value = 0
    dut.rd_en.value = 0
    dut.rd_addr.value = 0
    dut.wr_en.value = 0
    dut.wr_addr.value = 0
    dut.wr_data.value = 0
    dut.reset_n.value = 0
    await ClockCycles(dut.clk, 3)
    dut.reset_n.value = 1
    await ClockCycles(dut.clk, 2)


async def pulse(dut, sig, val):
    sig.value = val
    await RisingEdge(dut.clk)
    sig.value = 0
    await RisingEdge(dut.clk)


async def rd(dut, addr):
    dut.rd_addr.value = addr
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)          # settle the combinational read
    return int(dut.rd_data.value)


async def wr(dut, addr, data):
    dut.wr_addr.value = addr
    dut.wr_data.value = data
    dut.wr_en.value = 1
    await RisingEdge(dut.clk)
    dut.wr_en.value = 0
    await RisingEdge(dut.clk)


@cocotb.test()
async def telemetry(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset(dut)
    rng = random.Random(9)

    # counters: pulse a few events on each lane, confirm the count
    expect = [0] * NCTR
    for _ in range(20):
        lane = rng.randrange(NCTR)
        await pulse(dut, dut.evt, 1 << lane)
        expect[lane] += 1
    for i in range(NCTR):
        got = await rd(dut, i)
        assert got == expect[i], f"ctr[{i}]={got} exp {expect[i]}"

    # sticky flags: set a few, read, write-1-to-clear a subset
    await pulse(dut, dut.flag_set, 0x000D)        # bits 0,2,3
    assert (await rd(dut, 0x20)) & 0xFFFF == 0x000D
    await wr(dut, 0x31, 0x0004)                    # clear bit 2
    assert (await rd(dut, 0x20)) & 0xFFFF == 0x0009

    # probe mux: distinct value per lane, select each at runtime
    vals = [rng.randrange(1 << 32) for _ in range(NPROBE)]
    dut.probe_in.value = sum(v << (32 * i) for i, v in enumerate(vals))
    await RisingEdge(dut.clk)
    for sel in range(NPROBE):
        await wr(dut, 0x30, sel)
        assert (await rd(dut, 0x22)) == sel, f"probe_sel readback {sel}"
        got = await rd(dut, 0x21)
        assert got == vals[sel], f"probe lane {sel}: {got:#x} exp {vals[sel]:#x}"

    dut._log.info(f"dbg_telemetry OK ({NCTR} counters, sticky clear, {NPROBE}-lane probe mux)")
