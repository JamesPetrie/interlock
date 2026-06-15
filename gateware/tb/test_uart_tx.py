"""uart_tx — 8N1 serializer. Sends bytes and decodes them back off the txd line
(sampling at mid-bit), confirming start/8-data-LSB-first/stop framing."""
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge

DIV = 8   # must match the build parameter (small for fast sim)


async def reset(dut):
    dut.data.value = 0
    dut.valid.value = 0
    dut.reset_n.value = 0
    await ClockCycles(dut.clk, 3)
    dut.reset_n.value = 1
    await ClockCycles(dut.clk, 2)


async def send(dut, b):
    dut.data.value = b
    dut.valid.value = 1
    await RisingEdge(dut.clk)          # accepted this edge (ready was high)
    dut.valid.value = 0


async def uart_decode(dut):
    """Sample txd: wait for start, step to mid of each bit, read 8 data bits LSB-first."""
    while dut.txd.value == 1:
        await RisingEdge(dut.clk)
    for _ in range(DIV + DIV // 2):    # start bit + half into d0
        await RisingEdge(dut.clk)
    byte = 0
    for i in range(8):
        byte |= int(dut.txd.value) << i
        for _ in range(DIV):
            await RisingEdge(dut.clk)
    stop = int(dut.txd.value)
    return byte, stop


@cocotb.test()
async def serialize(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset(dut)
    rng = random.Random(3)
    for b in [0x00, 0xFF, 0x55, 0xA3, ord('K')] + [rng.randrange(256) for _ in range(8)]:
        dec = cocotb.start_soon(uart_decode(dut))
        await send(dut, b)
        byte, stop = await dec
        assert byte == b, f"byte {byte:#04x} != {b:#04x}"
        assert stop == 1, "stop bit not high"
        while not dut.ready.value:     # back to idle before next byte
            await RisingEdge(dut.clk)
    dut._log.info("uart_tx OK (8N1 framing decoded for 13 bytes)")
