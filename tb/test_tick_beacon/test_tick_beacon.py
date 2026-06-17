"""tick_beacon: every STRIDE bucket_tick pulses it emits a 32-byte beacon body
(magic|iid|bucket|tick_period_ns|stride). We hold b_ready low through the ticks so
the emitter stalls holding the first byte, then release and capture the 32 bytes
and check them byte-for-byte. A second beacon confirms the reported bucket advances
by STRIDE. Defaults match tick_beacon's params (IID=7, period=1ms, stride=16)."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge

IID, TICK_PERIOD_NS, STRIDE = 7, 1_000_000, 16
MAGIC = b"ilbcn-v1"


def expected_body(bucket):
    return (MAGIC + IID.to_bytes(8, "big") + bucket.to_bytes(8, "big")
            + TICK_PERIOD_NS.to_bytes(4, "big") + STRIDE.to_bytes(4, "big"))


async def reset(dut):
    dut.bucket_tick.value = 0
    dut.b_ready.value = 0           # hold the beacon so it stalls on the first byte
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 3)


async def pulse_tick(dut):
    dut.bucket_tick.value = 1
    await RisingEdge(dut.clk)
    dut.bucket_tick.value = 0
    await RisingEdge(dut.clk)


async def capture(dut, n=32):
    """With b_ready high, collect n beacon bytes until b_last."""
    dut.b_ready.value = 1
    buf = bytearray()
    while True:
        await ReadOnly()
        if dut.b_valid.value:
            buf.append(int(dut.b_data.value))
            last = bool(dut.b_last.value)
            await RisingEdge(dut.clk)
            if last:
                break
        else:
            await RisingEdge(dut.clk)
    dut.b_ready.value = 0
    return bytes(buf)


async def beacon_after_stride(dut, expect_bucket):
    for _ in range(STRIDE):
        await pulse_tick(dut)
    await ReadOnly()
    assert dut.b_valid.value, "no beacon emitted after STRIDE ticks"
    await RisingEdge(dut.clk)
    body = await capture(dut)
    exp = expected_body(expect_bucket)
    assert body == exp, f"\n got={body.hex()}\n exp={exp.hex()}"
    return body


@cocotb.test()
async def beacon_body_and_advance(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset(dut)
    b1 = await beacon_after_stride(dut, STRIDE)          # first beacon: bucket = STRIDE
    assert b1[:8] == MAGIC
    await ClockCycles(dut.clk, 5)
    await beacon_after_stride(dut, 2 * STRIDE)           # second: bucket advanced by STRIDE
    dut._log.info("beacon_body_and_advance: 32B bodies correct, bucket %d then %d"
                  % (STRIDE, 2 * STRIDE))


@cocotb.test()
async def no_beacon_before_stride(dut):
    """No beacon until STRIDE ticks accumulate."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset(dut)
    for _ in range(STRIDE - 1):
        await pulse_tick(dut)
    await ReadOnly()
    assert not dut.b_valid.value, "beacon emitted before STRIDE ticks"
    await RisingEdge(dut.clk)                            # leave ReadOnly before driving
    await pulse_tick(dut)                                # the STRIDE-th tick
    await ReadOnly()
    assert dut.b_valid.value, "no beacon on the STRIDE-th tick"
    dut._log.info("no_beacon_before_stride: beacon only on the STRIDE-th tick")
