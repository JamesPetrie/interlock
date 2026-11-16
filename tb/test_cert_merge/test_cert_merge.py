"""cocotb testbench for cert_merge.sv — the 3x1 AXIS packet arbiter.

Drives the packet port (s0) and the two certificate ports (s1, s2) with
tagged packets and checks the merged master stream:

  * packets are atomic — beats of different inputs never interleave (every
    byte of an output packet carries its source's tag);
  * per-source order is preserved, and nothing is lost or invented;
  * tuser passes through with the granted stream (length checked @ beat #0);
  * the master obeys AXIS stability: while tvalid && !tready the presented
    beat must not change (this is what the grant locking is for).
"""
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, Combine

PORTS = (0, 1, 2)


def tagged_packet(port: int, seq: int, nbytes: int) -> bytes:
    """Every byte carries the source port tag in its top 2 bits."""
    rng = random.Random(1000 * port + seq)
    return bytes((port << 6) | (rng.getrandbits(6)) for _ in range(nbytes))


def pack_axis(data: bytes):
    """bytes -> [(tdata, tkeep, tlast, tuser)]; len @ beat #0."""
    n = (len(data) + 3) // 4
    beats = []
    for w in range(n):
        chunk = data[4 * w: 4 * w + 4]
        d = keep = 0
        for k, b in enumerate(chunk):
            d |= b << (8 * k)
            keep |= 1 << k
        beats.append((d, keep, int(w == n - 1),
                      len(data) if w == 0 else 0))
    return beats


async def drive_port(dut, port: int, pkts, gap=0, rng=None):
    """Drive packets into slave port `port`, `gap` idle cycles between."""
    tvalid = getattr(dut, f"tvalid_s{port}")
    tready = getattr(dut, f"tready_s{port}")
    tdata = getattr(dut, f"tdata_s{port}")
    tkeep = getattr(dut, f"tkeep_s{port}")
    tlast = getattr(dut, f"tlast_s{port}")
    tuser = getattr(dut, f"tuser_s{port}")
    for data in pkts:
        for d, keep, last, user in pack_axis(data):
            tvalid.value = 1
            tdata.value = d
            tkeep.value = keep
            tlast.value = last
            tuser.value = user
            while True:
                await ReadOnly()
                ready = int(tready.value)
                await RisingEdge(dut.clk)
                if ready:
                    break
        tvalid.value = 0
        tlast.value = 0
        n = gap if rng is None else rng.randint(0, gap)
        for _ in range(n):
            await RisingEdge(dut.clk)


async def recv_master(dut, n_packets, throttle=0.0, rng=None,
                      max_cycles=200_000):
    """Collect n packets from the master: (bytes, tuser@beat0)."""
    rng = rng or random.Random(0)
    pkts, cur, tuser0 = [], bytearray(), None
    for _ in range(max_cycles):
        if len(pkts) == n_packets:
            break
        dut.tready_m.value = 0 if rng.random() < throttle else 1
        await ReadOnly()
        if int(dut.tvalid_m.value) and int(dut.tready_m.value):
            d, keep = int(dut.tdata_m.value), int(dut.tkeep_m.value)
            if tuser0 is None:
                tuser0 = int(dut.tuser_m.value)
            for k in range(4):
                if (keep >> k) & 1:
                    cur.append((d >> (8 * k)) & 0xFF)
            if int(dut.tlast_m.value):
                pkts.append((bytes(cur), tuser0))
                cur, tuser0 = bytearray(), None
        await RisingEdge(dut.clk)
    else:
        raise TimeoutError(f"only {len(pkts)}/{n_packets} packets")
    dut.tready_m.value = 1
    return pkts


async def stability_monitor(dut):
    """While tvalid_m && !tready_m, the presented beat must not change."""
    held = None
    while True:
        await ReadOnly()
        beat = (int(dut.tdata_m.value), int(dut.tkeep_m.value),
                int(dut.tlast_m.value), int(dut.tuser_m.value))
        if held is not None:
            assert int(dut.tvalid_m.value), "tvalid_m dropped before handshake"
            assert beat == held, f"beat changed under stall: {beat} != {held}"
        stalled = int(dut.tvalid_m.value) and not int(dut.tready_m.value)
        held = beat if stalled else None
        await RisingEdge(dut.clk)


async def reset(dut):
    cocotb.start_soon(Clock(dut.clk, 8, unit="ns").start())
    dut.rst_n.value = 0
    for p in PORTS:
        getattr(dut, f"tvalid_s{p}").value = 0
        getattr(dut, f"tdata_s{p}").value = 0
        getattr(dut, f"tkeep_s{p}").value = 0
        getattr(dut, f"tlast_s{p}").value = 0
        getattr(dut, f"tuser_s{p}").value = 0
    dut.tready_m.value = 1
    for _ in range(5):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    for _ in range(2):
        await RisingEdge(dut.clk)


def check_merge(got, sent_per_port):
    """Atomicity, completeness, per-source order, tuser pass-through."""
    seen_per_port = {p: [] for p in PORTS}
    for data, tuser0 in got:
        tags = {b >> 6 for b in data}
        assert len(tags) == 1, f"interleaved packet: tags {tags}"
        assert tuser0 == len(data), f"tuser@beat0 {tuser0} != {len(data)}"
        seen_per_port[tags.pop()].append(data)
    for p in PORTS:
        assert seen_per_port[p] == sent_per_port[p], f"port {p} stream differs"


async def run_case(dut, sent_per_port, gaps, throttle=0.0):
    cocotb.start_soon(stability_monitor(dut))
    total = sum(len(v) for v in sent_per_port.values())
    drv = [cocotb.start_soon(drive_port(dut, p, sent_per_port[p],
                                        gap=gaps[p],
                                        rng=random.Random(50 + p)))
           for p in PORTS]
    rx = cocotb.start_soon(recv_master(dut, total, throttle=throttle,
                                       rng=random.Random(3)))
    await Combine(rx, *drv)
    check_merge(rx.result(), sent_per_port)


# ---------------------------------------------------------------------- tests

@cocotb.test()
async def test_packets_only(dut):
    """Cert ports idle: the packet stream passes through unchanged."""
    await reset(dut)
    pkts = [tagged_packet(0, i, 5 + 13 * i) for i in range(6)]
    await run_case(dut, {0: pkts, 1: [], 2: []}, gaps={0: 0, 1: 0, 2: 0})


@cocotb.test()
async def test_cert_interleave(dut):
    """Certs on both ports splice into a continuous packet stream at packet
    boundaries; everything arrives whole and in per-source order."""
    await reset(dut)
    sent = {0: [tagged_packet(0, i, 20 + 7 * i) for i in range(10)],
            1: [tagged_packet(1, i, 104) for i in range(3)],
            2: [tagged_packet(2, i, 104) for i in range(3)]}
    await run_case(dut, sent, gaps={0: 0, 1: 40, 2: 60})


@cocotb.test()
async def test_backpressure(dut):
    """Master throttled while all three ports contend."""
    await reset(dut)
    sent = {0: [tagged_packet(0, i, 9 + 5 * i) for i in range(8)],
            1: [tagged_packet(1, i, 104) for i in range(2)],
            2: [tagged_packet(2, i, 104) for i in range(2)]}
    await run_case(dut, sent, gaps={0: 4, 1: 30, 2: 30}, throttle=0.4)


@cocotb.test()
async def test_single_beat_packets(dut):
    """Single-beat packets (tlast on the first beat) release the grant in
    the same cycle they take it."""
    await reset(dut)
    sent = {0: [tagged_packet(0, i, 4) for i in range(8)],
            1: [tagged_packet(1, 0, 4)],
            2: [tagged_packet(2, 0, 4)]}
    await run_case(dut, sent, gaps={0: 1, 1: 9, 2: 13})
