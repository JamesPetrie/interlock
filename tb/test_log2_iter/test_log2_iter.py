"""cocotb testbench for log2_iter.sv — iterative binary logarithm.

The DUT computes log2 of a custom-float probability (recomp_pkg: value =
1.mant x 2^-exp) as a signed Q(LOG2_W-LOG2_FRAC_W).LOG2_FRAC_W fixed-point
result by repeated squaring of the significand, one fraction bit per cycle.

Two references: a bit-exact Python replica of the truncating iteration
(any mismatch is a bug) and math.log2 with the documented one-sided error
bound (output truncation plus compounded significand truncation).
"""
import math
import random
import re
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import NextTimeStep, ReadOnly, RisingEdge

# recomp_pkg geometry
EXP_W   = 6
MANT_W  = 32 - EXP_W
EXP_MAX = (1 << EXP_W) - 1

# log2 fraction bits, parsed from the package so edits track automatically
_PKG = Path(__file__).resolve().parents[2] / "gateware/src/src_hdl/recomp_pkg.sv"
FRAC_W = int(re.search(r"LOG2_FRAC_W\s*=\s*(\d+)", _PKG.read_text()).group(1))

# guard-bit significand width of the DUT iteration (see log2_iter.sv header);
# GUARD_W mirrors the DUT's $clog2(LOG2_FRAC_W) + 3
GUARD_W = (FRAC_W - 1).bit_length() + 3
XW = max(FRAC_W + GUARD_W, MANT_W)

# one-sided error bound vs true log2: output truncation plus the compounded
# squaring loss of 1.44*2^-GUARD_W ulp
EPS = (1 + 1.443 * 2.0 ** -GUARD_W) * 2.0 ** -FRAC_W


def prob_word(exp, mant):
    assert 0 <= exp <= EXP_MAX and 0 <= mant < (1 << MANT_W)
    return (exp << MANT_W) | mant


def model_log2(exp, mant):
    """Bit-exact replica of the DUT iteration; signed Q.FRAC_W integer."""
    x = ((1 << MANT_W) | mant) << (XW - MANT_W)   # Q1.XW, in [1,2)
    frac = 0
    for _ in range(FRAC_W):
        sq = x * x                                # Q2.2XW, in [1,4)
        if sq >> (2 * XW + 1):                    # square >= 2
            frac = (frac << 1) | 1
            x = sq >> (XW + 1)
        else:
            frac <<= 1
            x = sq >> XW
    return (-exp << FRAC_W) + frac


def true_log2(exp, mant):
    return math.log2((1 << MANT_W) + mant) - MANT_W - exp


def sext(v, w):
    return v - (1 << w) if v >> (w - 1) else v


# --------------------------------------------------------------------------
async def reset(dut):
    cocotb.start_soon(Clock(dut.clk, 8, unit="ns").start())
    dut.rst_n.value = 0
    dut.start.value = 0
    dut.p.value = 0
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


async def wait_done(dut):
    """Wait for the done pulse; returns res as a signed int."""
    for _ in range(FRAC_W + 4):
        await ReadOnly()
        if int(dut.done.value):
            res = sext(int(dut.res.value), 32)
            await RisingEdge(dut.clk)
            return res
        await RisingEdge(dut.clk)
    raise AssertionError(f"no done within {FRAC_W + 4} cycles")


async def compute(dut, exp, mant):
    """Pulse start, wait for done; returns res as a signed int."""
    dut.p.value = prob_word(exp, mant)
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0
    return await wait_done(dut)


def check(exp, mant, res):
    mdl = model_log2(exp, mant)
    assert res == mdl, f"({exp},{mant}): res={res}, model={mdl}"
    got = res / (1 << FRAC_W)
    ref = true_log2(exp, mant)
    assert ref - EPS <= got <= ref, \
        f"({exp},{mant}): {got} vs true {ref} (eps={EPS})"


# --------------------------------------------------------------------------
@cocotb.test()
async def test_known_values(dut):
    """Exact powers of two and simple mantissas land where they must."""
    await reset(dut)
    assert await compute(dut, 0, 0) == 0                    # log2(1.0)
    assert await compute(dut, 1, 0) == -1 << FRAC_W         # log2(0.5)
    assert await compute(dut, EXP_MAX, 0) == -EXP_MAX << FRAC_W
    res = await compute(dut, 0, 1 << (MANT_W - 1))          # log2(1.5) > 0
    check(0, 1 << (MANT_W - 1), res)
    assert res > 0
    res = await compute(dut, 1, 1 << (MANT_W - 1))          # log2(0.75)
    check(1, 1 << (MANT_W - 1), res)


@cocotb.test()
async def test_start_ignored_while_busy(dut):
    """A start mid-computation must not corrupt or restart the result."""
    await reset(dut)
    dut.p.value = prob_word(5, 777)
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.p.value = prob_word(0, 0)                     # held with start high
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.start.value = 0
    res = await wait_done(dut)
    assert res == model_log2(5, 777), "busy start corrupted the result"


@cocotb.test()
async def test_back_to_back(dut):
    """start on the done cycle is accepted."""
    await reset(dut)
    a, b = (7, 4242), (0, 31337)
    dut.p.value = prob_word(*a)
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0
    for _ in range(FRAC_W + 4):
        await ReadOnly()
        if int(dut.done.value):
            break
        await RisingEdge(dut.clk)
    else:
        raise AssertionError("no first done")
    await NextTimeStep()                              # writable again
    dut.p.value = prob_word(*b)                       # relaunch on done cycle
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0
    assert await wait_done(dut) == model_log2(*b)


@cocotb.test()
async def test_fuzz(dut):
    """Random probabilities against the bit-exact model and math.log2."""
    await reset(dut)
    rng = random.Random(42)
    for _ in range(200):
        exp = rng.randint(0, EXP_MAX)
        mant = rng.randrange(1 << MANT_W)
        check(exp, mant, await compute(dut, exp, mant))
