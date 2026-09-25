"""ilock_pl built with the production core (fabric_bridge) plus the runtime
pass-through switch: mode_core = 0 behaves like the pass-through build with the
core held in reset; mode_core = 1 hands both ports to the core; switching back
restores the pass-through."""
import random

import cocotb
from cocotb.triggers import ClockCycles

from axis_util import rand_pkt, wait_for
from test_ilock_pl_passthru import setup


async def send_pair(dut, s0, s1, k0, k1, rng, n=6):
    a = [rand_pkt(rng, 60, 600) for _ in range(n)]
    b = [rand_pkt(rng, 60, 600) for _ in range(n)]
    k0.packets.clear(); k1.packets.clear()
    for i in range(n):
        await s0.send(a[i])
        await s1.send(b[i])
        await ClockCycles(dut.core_clk, 200)
    await wait_for(lambda: len(k1.packets) == n and len(k0.packets) == n, dut.core_clk, max_cycles=60_000, what="frames crossed")
    assert k1.packets == a and k0.packets == b, "crossover frames differ"


@cocotb.test()
async def bypass_then_core_then_bypass(dut):
    rng = random.Random(90)
    s0, s1, k0, k1 = await setup(dut)                    # mode_core = 0, crossover
    await ClockCycles(dut.core_clk, 4)
    assert int(dut.dut.sel_core.value) == 0 and int(dut.dut.core_rst_n_eff.value) == 0, "core held in reset while bypassed"
    await send_pair(dut, s0, s1, k0, k1, rng)

    dut.mode_core.value = 1
    await ClockCycles(dut.core_clk, 4)
    assert int(dut.dut.sel_core.value) == 1 and int(dut.dut.core_rst_n_eff.value) == 1, "core selected and released"
    k0.packets.clear(); k1.packets.clear()
    probe = rand_pkt(rng, 100, 100)
    await s0.send(probe)                                  # goes to the core, not to the switch
    await ClockCycles(dut.core_clk, 3000)
    assert probe not in k1.packets, "the pass-through switch must be disconnected in core mode"

    dut.mode_core.value = 0
    await ClockCycles(dut.core_clk, 4)
    assert int(dut.dut.sel_core.value) == 0 and int(dut.dut.core_rst_n_eff.value) == 0
    await send_pair(dut, s0, s1, k0, k1, rng)
