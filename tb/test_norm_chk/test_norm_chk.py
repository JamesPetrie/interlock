"""cocotb testbench for norm_chk.sv — exact normalization check.

The DUT accumulates custom-float probabilities (recomp_pkg: value =
1.mant x 2^-exp, no zero encoding — the smallest value is 2^-EXP_MAX) into a
full-range fixed-point accumulator and latches `fail` once the running sum
exceeds 1.0.

The reference model uses Fraction, so it is exact: any divergence between
the DUT's wide accumulator and true rational arithmetic is a bug.
"""
import os
import random
from fractions import Fraction

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly

# recomp_pkg geometry
EXP_W   = 6
MANT_W  = 32 - EXP_W
EXP_MAX = (1 << EXP_W) - 1           # largest exponent (no zero encoding)
# norm_chk's catch-all space width, pinned from canon_pkg (Makefile derives it)
SPACE_W = 8 * int(os.environ.get("TOK_BYTES", "4"))

ONE = Fraction(1)


def prob_word(exp, mant):
    assert 0 <= exp <= EXP_MAX and 0 <= mant < (1 << MANT_W)
    return (exp << MANT_W) | mant


def prob_val(exp, mant):
    return Fraction((1 << MANT_W) + mant, 1 << (MANT_W + exp))


def model_fail_seq(entries):
    """Sticky fail after each add (DUT ignores adds once failed).

    An entry is (exp, mant) or (exp, mant, True) — the latter is a
    catch-all, charged at value x 2^SPACE_W.
    """
    s, failed, out = Fraction(0), False, []
    for e in entries:
        exp, mant, ca = e if len(e) == 3 else (*e, False)
        if not failed:
            s += prob_val(exp, mant) * ((1 << SPACE_W) if ca else 1)
            failed = s > ONE
        out.append(failed)
    return out


# --------------------------------------------------------------------------
async def reset(dut):
    cocotb.start_soon(Clock(dut.clk, 8, unit="ns").start())
    dut.rst_n.value = 0
    dut.clr.value = 0
    dut.add.value = 0
    dut.catchall.value = 0
    dut.p.value = 0
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


async def read_fail(dut):
    """Wait for the pipeline to drain (busy low), then read fail.

    No latency assumption: the busy handshake is the contract.
    """
    while True:
        await ReadOnly()
        if not int(dut.busy.value):
            return int(dut.fail.value)
        await RisingEdge(dut.clk)


async def run_estimate(dut, entries, per_add_check=True):
    """clr, then add each entry back-to-back; returns the final fail.

    Entries are (exp, mant) plain adds or (exp, mant, True) catch-alls.
    """
    exp_seq = model_fail_seq(entries)
    dut.clr.value = 1
    await RisingEdge(dut.clk)
    dut.clr.value = 0
    for k, e in enumerate(entries):
        exp, mant, ca = e if len(e) == 3 else (*e, False)
        dut.p.value = prob_word(exp, mant)
        dut.catchall.value = int(ca)
        dut.add.value = 1
        await RisingEdge(dut.clk)          # add issued
        if per_add_check:
            dut.add.value = 0
            got = await read_fail(dut)
            assert got == exp_seq[k], \
                f"add {k} {entries[k]}: fail={got}, model={exp_seq[k]}"
            await RisingEdge(dut.clk)
            dut.add.value = 1
    dut.add.value = 0
    dut.catchall.value = 0
    got = await read_fail(dut)
    await RisingEdge(dut.clk)
    exp_final = exp_seq[-1] if entries else False
    assert got == exp_final, f"final: fail={got}, model={exp_final}"
    return got


# entry shorthands
P_ONE   = (0, 0)                     # 1.0
P_HALF  = (1, 0)                     # 0.5
P_LSB   = (EXP_MAX, 0)               # 2^-63, smallest representable
P_BELOW = (1, (1 << MANT_W) - 1)     # 1 - 2^-(MANT_W+1), just under 1.0


# --------------------------------------------------------------------------
@cocotb.test()
async def test_exact_one(dut):
    """Sum == 1.0 passes; one extra LSB tips it over."""
    await reset(dut)
    assert await run_estimate(dut, [P_ONE]) == 0
    assert await run_estimate(dut, [P_ONE, P_LSB]) == 1


@cocotb.test()
async def test_halves(dut):
    """0.5 + 0.5 == 1.0 exactly; the LSB on top fails."""
    await reset(dut)
    assert await run_estimate(dut, [P_HALF, P_HALF]) == 0
    assert await run_estimate(dut, [P_HALF, P_HALF, P_LSB]) == 1


@cocotb.test()
async def test_boundary_dust(dut):
    """(1 - 2^-27) + 2^-27 lands exactly on 1.0 — no rounding leak."""
    await reset(dut)
    dust = (MANT_W + 1, 0)                        # 2^-(MANT_W+1)
    assert await run_estimate(dut, [P_BELOW, dust]) == 0
    assert await run_estimate(dut, [P_BELOW, dust, P_LSB]) == 1


@cocotb.test()
async def test_oversized_prob(dut):
    """exp = 0, mant != 0 encodes > 1.0 and must fail by itself."""
    await reset(dut)
    assert await run_estimate(dut, [(0, 1)]) == 1


@cocotb.test()
async def test_many_small(dut):
    """1024 x 2^-10 == 1.0 passes; the 1025th fails."""
    await reset(dut)
    p = (10, 0)
    assert await run_estimate(dut, [p] * 1024, per_add_check=False) == 0
    assert await run_estimate(dut, [p] * 1025, per_add_check=False) == 1


@cocotb.test()
async def test_sticky_and_clr(dut):
    """fail holds through further adds; clr re-arms cleanly."""
    await reset(dut)
    assert await run_estimate(dut, [P_ONE, P_ONE, P_HALF, P_LSB]) == 1
    assert await run_estimate(dut, [P_HALF, P_HALF]) == 0


@cocotb.test()
async def test_catchall(dut):
    """Catch-all charged at p x 2^SPACE_W: 2^-(W+1) over the space is 0.5."""
    await reset(dut)
    ca = (SPACE_W + 1, 0, True)             # 2^-(W+1) x 2^W = 0.5
    assert await run_estimate(dut, [P_HALF, ca]) == 0          # exactly 1.0
    assert await run_estimate(dut, [P_HALF, ca, P_LSB]) == 1   # one LSB over


@cocotb.test()
async def test_catchall_ovf(dut):
    """exp < W: the catch-all charge alone exceeds 1.0 -> immediate fail."""
    await reset(dut)
    assert await run_estimate(dut, [(SPACE_W - 1, 0, True)]) == 1   # 2.0
    assert await run_estimate(dut, [(0, 0, True)]) == 1              # 2^W


@cocotb.test()
async def test_catchall_exact_one(dut):
    """exp == W: charge is 1.mant — exactly 1.0 passes, any mantissa fails."""
    await reset(dut)
    assert await run_estimate(dut, [(SPACE_W, 0, True)]) == 0
    assert await run_estimate(dut, [(SPACE_W, 1, True)]) == 1


@cocotb.test()
async def test_fuzz(dut):
    """Random estimates against the exact Fraction model, per-add."""
    await reset(dut)
    rng = random.Random(42)
    for _ in range(60):
        n = rng.randint(1, 12)
        entries = []
        for _ in range(n):
            if rng.random() < 0.7:                       # borderline region
                exp = rng.randint(0, 4)
            else:                                        # deep tail
                exp = rng.randint(5, EXP_MAX)
            entries.append((exp, rng.randrange(1 << MANT_W)))
        if rng.random() < 0.5:                           # catch-all last pair
            exp = rng.randint(max(0, SPACE_W - 2), EXP_MAX)
            entries.append((exp, rng.randrange(1 << MANT_W), True))
        await run_estimate(dut, entries)
