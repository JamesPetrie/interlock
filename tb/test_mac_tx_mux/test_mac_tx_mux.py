"""mac_tx_mux: 3 MAC-TX sources onto one output. Frames must stay atomic (no
interleave across sof..eof) and cert sources (1,2) must win arbitration over
forwarded traffic (0)."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge, Combine


async def reset(dut):
    cocotb.start_soon(Clock(dut.clk, 8, unit="ns").start())
    for p in ("in0", "in1", "in2", "in3"):
        getattr(dut, p + "_rdy").value = 0
        getattr(dut, p + "_sof").value = 0
        getattr(dut, p + "_eof").value = 0
        getattr(dut, p + "_dat").value = 0
        getattr(dut, p + "_bv").value = 0
    dut.out_acpt.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)


async def drive(dut, pfx, words):
    """words = list of 32-bit data; sof on first, eof on last, bv=0."""
    rdy = getattr(dut, pfx + "_rdy"); sof = getattr(dut, pfx + "_sof")
    eof = getattr(dut, pfx + "_eof"); dat = getattr(dut, pfx + "_dat")
    acpt = getattr(dut, pfx + "_acpt")
    for i, w in enumerate(words):
        dat.value = w
        sof.value = 1 if i == 0 else 0
        eof.value = 1 if i == len(words) - 1 else 0
        rdy.value = 1
        while True:
            await ReadOnly()
            a = int(acpt.value)
            await RisingEdge(dut.clk)
            if a:
                break
    rdy.value = 0; sof.value = 0; eof.value = 0


async def sink(dut, n_frames):
    """Collect frames as lists of words; assert each is a clean sof..eof run."""
    frames = []
    cur = []
    in_frame = False
    while len(frames) < n_frames:
        dut.out_acpt.value = 1
        await ReadOnly()
        rdy = int(dut.out_rdy.value)
        sof = int(dut.out_sof.value)
        eof = int(dut.out_eof.value)
        dat = int(dut.out_dat.value)
        await RisingEdge(dut.clk)
        if rdy:
            assert sof == (not in_frame), f"SOF framing: in_frame={in_frame} sof={sof}"
            cur.append(dat)
            in_frame = True
            if eof:
                frames.append(cur)
                cur = []
                in_frame = False
    dut.out_acpt.value = 0
    return frames


@cocotb.test()
async def atomic_and_priority(dut):
    await reset(dut)
    fwd = [0xA0, 0xA1, 0xA2]          # source 0 (forwarded), 3 words
    cert = [0xB0, 0xB1]              # source 1 (cert), 2 words
    rx = cocotb.start_soon(sink(dut, 2))
    t0 = cocotb.start_soon(drive(dut, "in0", fwd))
    t1 = cocotb.start_soon(drive(dut, "in1", cert))
    await Combine(t0, t1, rx)
    frames = rx.result()
    assert [0xB0, 0xB1] in frames and [0xA0, 0xA1, 0xA2] in frames, f"frames={frames}"
    # cert (source 1) has priority -> it should come out first
    assert frames[0] == [0xB0, 0xB1], f"cert should win arbitration, got {frames[0]}"
    dut._log.info("atomic_and_priority: both frames whole; cert won arbitration")


@cocotb.test()
async def three_way(dut):
    await reset(dut)
    rx = cocotb.start_soon(sink(dut, 3))
    t0 = cocotb.start_soon(drive(dut, "in0", [0x10, 0x11]))
    t1 = cocotb.start_soon(drive(dut, "in1", [0x20]))
    t2 = cocotb.start_soon(drive(dut, "in2", [0x30, 0x31, 0x32]))
    await Combine(t0, t1, t2, rx)
    frames = rx.result()
    assert [0x10, 0x11] in frames and [0x20] in frames and [0x30, 0x31, 0x32] in frames, \
        f"frames={frames}"
    dut._log.info("three_way: all three frames forwarded whole")


@cocotb.test()
async def beacon_lowest_priority(dut):
    """Beacon (in3) is the lowest priority: forwarded traffic (in0) wins over it,
    but both frames still pass whole."""
    await reset(dut)
    rx = cocotb.start_soon(sink(dut, 2))
    t0 = cocotb.start_soon(drive(dut, "in0", [0xF0, 0xF1]))   # forwarded
    t3 = cocotb.start_soon(drive(dut, "in3", [0xE0]))         # beacon
    await Combine(t0, t3, rx)
    frames = rx.result()
    assert [0xF0, 0xF1] in frames and [0xE0] in frames, f"frames={frames}"
    assert frames[0] == [0xF0, 0xF1], f"forwarded should beat beacon, got {frames[0]}"
    dut._log.info("beacon_lowest_priority: forwarded won arbitration over the beacon")
