"""Drive both batch_buffer versions with the same random streams (packets, empty
delimiters, drop flags, back-pressure, ticks) and require identical outputs on
every cycle, including X-freedom."""
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge


async def run(dut, seed, cycles):
    rng = random.Random(seed)
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst_n.value = 0
    for s in ("tvalid_s", "tdata_s", "tkeep_s", "tlast_s", "tuser_s", "tready_m", "tick", "timer"):
        getattr(dut, s).value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    words_left, in_pkt, timer = 0, False, 0
    beats_out, mism = 0, 0
    for cyc in range(cycles):
        # ---- next input beat (held while not accepted) ----
        if not in_pkt and rng.random() < 0.8:
            in_pkt = True
            words_left = rng.choice([1, 1, 2, 15, 16, 17, 60, 380, 400])
            if rng.random() < 0.08:                      # empty delimiter beat
                words_left = 0
        if in_pkt:
            dut.tvalid_s.value = 1 if rng.random() < 0.85 else 0
            last = words_left <= 1
            dut.tlast_s.value = int(last)
            dut.tkeep_s.value = 0 if words_left == 0 else (rng.choice([1, 3, 7, 15]) if last else 15)
            dut.tdata_s.value = rng.getrandbits(32)
            dut.tuser_s.value = rng.getrandbits(16) if last else 0
            if last and rng.random() < 0.1:
                dut.tuser_s.value = int(dut.tuser_s.value) | 1      # drop flag
        else:
            dut.tvalid_s.value = 0
        dut.tready_m.value = 1 if rng.random() < 0.7 else 0
        dut.tick.value = 1 if (cyc % 2500 == 2499) else 0
        timer = 0 if int(dut.tick.value) else timer + 1
        dut.timer.value = timer
        await ReadOnly()
        if int(dut.mismatch.value):
            mism += 1
            if mism <= 3:
                dut._log.error(f"cycle {cyc}: outputs differ (tready_s {dut.a_tready_s.value}, tvalid_m {dut.a_tvalid_m.value})")
        if int(dut.a_tvalid_m.value) and int(dut.tready_m.value):
            beats_out += 1
        accepted = int(dut.tvalid_s.value) and int(dut.a_tready_s.value)
        await RisingEdge(dut.clk)
        if accepted:
            if words_left <= 1:
                in_pkt = False
            else:
                words_left -= 1
    assert mism == 0, f"{mism} mismatching cycles"
    assert beats_out > 1000, f"only {beats_out} output beats: stimulus did not exercise the drain"
    dut._log.info(f"seed {seed}: {cycles} cycles identical, {beats_out} output beats")


@cocotb.test()
async def equiv_seed1(dut):
    await run(dut, 1, 40_000)


@cocotb.test()
async def equiv_seed2(dut):
    await run(dut, 2, 40_000)
