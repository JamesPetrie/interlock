"""telemetry_top — end-to-end CPU-free telemetry: drive datapath events/probes,
tap the ASCII byte stream the dumper feeds uart_tx, parse a full sweep, and
confirm it reports the counters / flags / probe lanes correctly."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge

NCTR, NPROBE = 8, 8


async def reset(dut):
    dut.evt.value = 0
    dut.flag_set.value = 0
    dut.probe_in.value = 0
    dut.reset_n.value = 0
    await ClockCycles(dut.clk, 3)
    dut.reset_n.value = 1
    await ClockCycles(dut.clk, 2)


async def pulse(dut, sig, val, n=1):
    for _ in range(n):
        sig.value = val
        await RisingEdge(dut.clk)
        sig.value = 0
        await RisingEdge(dut.clk)


async def collect(dut, nbytes):
    out = bytearray()
    while len(out) < nbytes:
        await ReadOnly()
        if dut.tx_valid.value and dut.tx_ready.value:
            out.append(int(dut.tx_data.value))
        await RisingEdge(dut.clk)
    return bytes(out)


@cocotb.test()
async def telemetry_e2e(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset(dut)

    # set up static datapath state to observe
    await pulse(dut, dut.evt, 1 << 0, n=3)            # ctr0 = 3
    await pulse(dut, dut.evt, 1 << 1, n=1)            # ctr1 = 1
    await pulse(dut, dut.flag_set, 0x000A)            # sticky flags = 0x000A
    probes = [0xA0000000 | i for i in range(NPROBE)]
    dut.probe_in.value = sum(v << (32 * i) for i, v in enumerate(probes))
    await RisingEdge(dut.clk)

    raw = await collect(dut, 360)                     # ~2 sweeps
    # take a guaranteed-complete sweep: bytes between the first two CRLFs
    first = raw.find(b"\r\n")
    second = raw.find(b"\r\n", first + 2)
    assert first >= 0 and second > first, "no complete sweep captured"
    fields = raw[first + 2:second].split()
    assert len(fields) == NCTR + 1 + NPROBE, f"got {len(fields)} fields"

    vals = [int(f, 16) for f in fields]
    ctrs, flags, probevals = vals[:NCTR], vals[NCTR], vals[NCTR + 1:]
    assert ctrs[0] == 3 and ctrs[1] == 1 and all(c == 0 for c in ctrs[2:]), f"counters {ctrs}"
    assert (flags & 0xFFFF) == 0x000A, f"flags {flags:#x}"
    assert probevals == probes, f"probes {[hex(p) for p in probevals]}"
    dut._log.info("telemetry_top OK — CPU-free UART sweep reports counters/flags/probes")
