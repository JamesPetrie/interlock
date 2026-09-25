"""ps_frame_port: the PS writes a frame into the TX buffer and starts N copies,
which come out on the MRMAC-shaped stream gap-free; frames arriving on the
input queue in the FIFO and are captured whole into the RX buffer one per
arm, in order; errored frames are dropped."""
import random
import struct

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge

from axis_util import AxisSource, AxisSink, rand_pkt, wait_for

NB = 48
ID, TX_LEN, TX_COUNT, TX_CTL, TX_STAT, TX_SENT = 0x00, 0x04, 0x08, 0x0C, 0x10, 0x14
RX_CTL, RX_STAT, RX_COUNT = 0x20, 0x24, 0x28
TXBUF, RXBUF = 0x1000, 0x2000


class AxiLite:
    def __init__(self, dut):
        self.d = dut
        for s in ("awvalid", "wvalid", "bready", "arvalid", "rready"):
            getattr(dut, s).value = 0

    async def write(self, addr, data):
        d = self.d
        d.awaddr.value, d.awvalid.value, d.wdata.value, d.wvalid.value = addr, 1, data, 1
        while True:
            await ReadOnly()
            ok = int(d.awready.value) and int(d.wready.value)
            await RisingEdge(d.aclk)
            if ok:
                break
        d.awvalid.value, d.wvalid.value, d.bready.value = 0, 0, 1
        while True:
            await ReadOnly()
            ok = int(d.bvalid.value)
            await RisingEdge(d.aclk)
            if ok:
                break
        d.bready.value = 0

    async def read(self, addr):
        d = self.d
        d.araddr.value, d.arvalid.value = addr, 1
        while True:
            await ReadOnly()
            ok = int(d.arready.value)
            await RisingEdge(d.aclk)
            if ok:
                break
        d.arvalid.value, d.rready.value = 0, 1
        while True:
            await ReadOnly()
            ok = int(d.rvalid.value)
            val = int(d.rdata.value)
            await RisingEdge(d.aclk)
            if ok:
                break
        d.rready.value = 0
        return val

    async def write_frame(self, base, pkt):
        padded = pkt + bytes(-len(pkt) % 4)
        for i in range(0, len(padded), 4):
            await self.write(base + i, struct.unpack("<I", padded[i:i + 4])[0])

    async def wait_stat(self, reg, mask, want, tries=60):
        """Poll a status register until (value & mask) == want (each read is a few cycles)."""
        for _ in range(tries):
            v = await self.read(reg)
            if (v & mask) == want:
                return v
            await ClockCycles(self.d.aclk, 50)
        raise AssertionError(f"register {reg:#x}: ({v:#x} & {mask:#x}) != {want:#x}")

    async def read_frame(self, base, n):
        words = [await self.read(base + 4 * i) for i in range((n + 3) // 4)]
        return b"".join(struct.pack("<I", w) for w in words)[:n]


async def setup(dut):
    cocotb.start_soon(Clock(dut.aclk, 10, unit="ns").start())
    cocotb.start_soon(Clock(dut.mac_clk, 3, unit="ns").start())
    dut.aresetn.value = 0
    dut.mac_rst_n.value = 0
    axi = AxiLite(dut)
    src = AxisSource(dut.mac_clk, dut.in_tvalid, None, dut.in_tdata, dut.in_tkeep, dut.in_tlast, dut.in_tuser, nbytes=NB)
    sink = AxisSink(dut.mac_clk, dut.out_tvalid, dut.out_tready, dut.out_tdata, dut.out_tkeep, dut.out_tlast, dut.out_tuser,
                    nbytes=NB, stall=0.3, check_hold=True, check_zero_pad=True)
    await ClockCycles(dut.aclk, 5)
    dut.aresetn.value = 1
    dut.mac_rst_n.value = 1
    await ClockCycles(dut.aclk, 5)
    cocotb.start_soon(sink.run())
    return axi, src, sink


@cocotb.test()
async def inject_frames(dut):
    rng = random.Random(80)
    axi, src, sink = await setup(dut)
    assert await axi.read(ID) == 0x50534650
    for n in (60, 61, 62, 63, 64, 1500):
        pkt = rand_pkt(rng, n, n)
        await axi.write_frame(TXBUF, pkt)
        await axi.write(TX_LEN, n)
        await axi.write(TX_COUNT, 3)
        sink.packets.clear()
        await axi.write(TX_CTL, 1)
        await wait_for(lambda: len(sink.packets) == 3, dut.aclk, max_cycles=50_000, what=f"3 frames of {n} bytes")
        assert sink.packets == [pkt] * 3, f"{n}-byte frames differ"
        await axi.wait_stat(TX_STAT, 1, 0)
        assert await axi.read(TX_SENT) == 3
    await ClockCycles(dut.mac_clk, 50)
    assert len(sink.packets) == 3, "no stray frames"


@cocotb.test()
async def capture_frames(dut):
    rng = random.Random(81)
    axi, src, sink = await setup(dut)
    a, b, c = rand_pkt(rng, 60, 1500), rand_pkt(rng, 61, 300), rand_pkt(rng, 100, 100)
    await axi.write(RX_CTL, 0b001)                       # arm
    await src.send(a)
    st = await axi.wait_stat(RX_STAT, 1, 1)
    assert (st >> 16) & 0x1FFF == len(a), "captured length"
    assert await axi.read_frame(RXBUF, len(a)) == a, "captured bytes"
    assert await axi.read(RX_COUNT) == 1
    await src.send(b)                                     # not armed: queued in the FIFO, not captured, not counted
    await src.send(c, err=True)                           # errored: dropped before the FIFO
    await src.send(c)                                     # queued behind b
    await ClockCycles(dut.aclk, 600)
    assert await axi.read(RX_COUNT) == 1
    assert await axi.read_frame(RXBUF, len(a)) == a, "buffer untouched while not armed"
    assert await axi.read(RX_STAT) & 0b100, "drops flag from the errored frame"
    await axi.write(RX_CTL, 0b011)                        # clear + arm -> b comes out of the queue
    st = await axi.wait_stat(RX_STAT, 1, 1)
    assert (st >> 16) & 0x1FFF == len(b), "b captured next"
    assert await axi.read_frame(RXBUF, len(b)) == b
    await axi.write(RX_CTL, 0b011)                        # next arm -> c (the good copy)
    st = await axi.wait_stat(RX_STAT, 1, 1)
    assert (st >> 16) & 0x1FFF == len(c)
    assert await axi.read_frame(RXBUF, len(c)) == c
    assert await axi.read(RX_COUNT) == 3, "three frames captured, the errored one never counted"
    await axi.write(RX_CTL, 0b011)                        # nothing left queued
    await ClockCycles(dut.aclk, 400)
    assert not (await axi.read(RX_STAT) & 1)


@cocotb.test()
async def capture_overflow(dut):
    rng = random.Random(82)
    axi, src, sink = await setup(dut)
    big = rand_pkt(rng, 4200, 4200)
    await axi.write(RX_CTL, 0b011)
    await src.send(big)
    st = await axi.wait_stat(RX_STAT, 1, 1)
    assert st & 0b10, "overflow flag"
    assert (st >> 16) & 0x1FFF == 4096, "length clamps to the buffer"
    assert await axi.read_frame(RXBUF, 4096) == big[:4096]
