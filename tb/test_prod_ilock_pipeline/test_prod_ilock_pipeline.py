"""End-to-end cocotb testbench for prod_ilock_pipeline.sv
(canon_proc -> batch_buffer -> traffic_commit).

Drives canonical packets into the AXI-Stream slave port (eth_deframe's
word-aligned DATA stream: tuser = truncation flag at tlast) and checks the
two AXI-Stream masters:

  * the packet port re-emits every accepted canonical packet byte-exactly
    (delayed into the following batch), with its length on tuser at beat #0;
    failing packets vanish — header failures are suppressed by canon_proc,
    payload/truncation failures travel as the tuser drop flag and are
    abandoned by the batch buffer;
  * the certificate port emits per-batch attestations — standalone digests
    d = SHA256(batch's packets) with contiguous signed counters (i, count)
    over exactly the accepted packets — ending with a cert covering all of
    them.

The batch geometry is shrunk via the parameter overrides in the Makefile;
BATCH_CYCLES below must match.
"""
import hashlib
import hmac as pyhmac
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, Combine

BATCH_CYCLES = 10000       # must match the Makefile override

CANON_HDR_BYTES = 12
INF = 1 << 63              # canonical ID MSB: inference packet

# traffic_commit session constants (key/epoch driven on the ports,
# DOMAIN_SEP module default, DIRECTION = 0 for DIR=REQ)
KEY = bytes.fromhex(
    "0f0e0d0c0b0a09080706050403020100"
    "1f1e1d1c1b1a19181716151413121110"
)
EPOCH = 7
DIRECTION = 0
DOMAIN_SEP = 0x41545354
M_BYTES = 64                                  # i8 count4 ts8 epoch4 dir4 domain4 digest32
CERT_BYTES = CANON_HDR_BYTES + M_BYTES + 32   # reserved hdr + m + tau = 108


def canonical_packet(can_id: int, pkt_idx: int, pld_len: int,
                     payload_len: int = None, seed: int = 0) -> bytes:
    """Canonical packet; payload_len != pld_len builds a lying header."""
    if payload_len is None:
        payload_len = pld_len
    rng = random.Random(seed)
    payload = bytes(rng.getrandbits(8) for _ in range(payload_len))
    return (can_id.to_bytes(8, "big") + pkt_idx.to_bytes(2, "big")
            + pld_len.to_bytes(2, "big") + payload)


def packet_for(seq: int, pld_len: int) -> bytes:
    """Sequence-valid inference canonical packet: ascending ID, pkt_idx 0."""
    return canonical_packet(INF | seq, 0, pld_len, seed=seq)


# ------------------------------------------------------------- cert reference

def check_cert(cert, pkts, prev_i):
    """Verify one cert frame: counters contiguous from prev_i, digest over
    exactly the accepted packets it claims; return its i."""
    data, length = cert
    assert length == CERT_BYTES, f"cert len@beat0 {length} != {CERT_BYTES}"
    assert len(data) == CERT_BYTES, f"cert is {len(data)} bytes"
    assert data[:CANON_HDR_BYTES] == bytes(CANON_HDR_BYTES), \
        f"reserved header not all-zero: {data[:CANON_HDR_BYTES].hex()}"
    m = data[CANON_HDR_BYTES:CANON_HDR_BYTES + M_BYTES]
    tau = data[CANON_HDR_BYTES + M_BYTES:]
    i = int.from_bytes(m[0:8], "big")
    count = int.from_bytes(m[8:12], "big")
    assert count >= 1 and i == prev_i + count, \
        f"cert counters not contiguous: i={i} count={count} prev_i={prev_i}"
    assert i <= len(pkts), f"cert i={i} outside the accepted-packet count"
    exp_d = hashlib.sha256(b"".join(pkts[prev_i:i])).digest()
    assert m[32:64] == exp_d, f"cert digest mismatch for packets {prev_i}..{i}"
    assert int.from_bytes(m[20:24], "big") == EPOCH
    assert int.from_bytes(m[24:28], "big") == DIRECTION
    assert int.from_bytes(m[28:32], "big") == DOMAIN_SEP
    assert tau == pyhmac.new(KEY, m, hashlib.sha256).digest(), "cert tau mismatch"
    return i


# --------------------------------------------------------------- AXIS helpers

def pack_axis(data: bytes, trunc: bool = False):
    """bytes -> [(tdata, tkeep, tlast, tuser)]; truncation flag @ tlast."""
    n = (len(data) + 3) // 4
    beats = []
    for w in range(n):
        chunk = data[4 * w: 4 * w + 4]
        d = keep = 0
        for k, b in enumerate(chunk):
            d |= b << (8 * k)
            keep |= 1 << k
        last = int(w == n - 1)
        beats.append((d, keep, last, int(last and trunc)))
    return beats


async def drive_pkt(dut, data: bytes, trunc: bool = False):
    for d, keep, last, user in pack_axis(data, trunc):
        dut.tvalid_s.value = 1
        dut.tdata_s.value = d
        dut.tkeep_s.value = keep
        dut.tlast_s.value = last
        dut.tuser_s.value = user
        while True:
            await ReadOnly()
            ready = int(dut.tready_s.value)
            await RisingEdge(dut.clk)
            if ready:
                break
    dut.tvalid_s.value = 0
    dut.tlast_s.value = 0
    dut.tuser_s.value = 0


async def recv_axis(dut, prefix, n_packets, throttle=0.0, rng=None,
                    max_cycles=20 * BATCH_CYCLES):
    """Collect n packets from an AXIS master port: (bytes, tuser@beat0)."""
    rng = rng or random.Random(0)
    tready = getattr(dut, f"tready_{prefix}")
    tvalid = getattr(dut, f"tvalid_{prefix}")
    tdata = getattr(dut, f"tdata_{prefix}")
    tkeep = getattr(dut, f"tkeep_{prefix}")
    tlast = getattr(dut, f"tlast_{prefix}")
    tuser = getattr(dut, f"tuser_{prefix}")
    pkts, cur, tuser0 = [], bytearray(), None
    for _ in range(max_cycles):
        if len(pkts) == n_packets:
            break
        tready.value = 0 if rng.random() < throttle else 1
        await ReadOnly()
        if int(tvalid.value) and int(tready.value):
            d, keep = int(tdata.value), int(tkeep.value)
            if tuser0 is None:
                tuser0 = int(tuser.value)
            for k in range(4):
                if (keep >> k) & 1:
                    cur.append((d >> (8 * k)) & 0xFF)
            if int(tlast.value):
                pkts.append((bytes(cur), tuser0))
                cur, tuser0 = bytearray(), None
        await RisingEdge(dut.clk)
    else:
        raise TimeoutError(f"only {len(pkts)}/{n_packets} packets on "
                           f"port {prefix} after {max_cycles} cycles")
    tready.value = 1
    return pkts


async def recv_certs_until(dut, pkts, final_i, throttle=0.0, rng=None):
    """Validate certs as they arrive until one covers all final_i packets."""
    last_i = 0
    while last_i != final_i:
        cert = (await recv_axis(dut, "c", 1, throttle=throttle, rng=rng))[0]
        last_i = check_cert(cert, pkts, last_i)


async def expect_quiet(dut, cycles=2 * BATCH_CYCLES):
    """No further output on either port (tready held high)."""
    dut.tready_m.value = 1
    dut.tready_c.value = 1
    for _ in range(cycles):
        await ReadOnly()
        assert int(dut.tvalid_m.value) == 0, "unexpected packet beat"
        assert int(dut.tvalid_c.value) == 0, "unexpected cert beat"
        await RisingEdge(dut.clk)


async def reset(dut, period_ns=8):
    cocotb.start_soon(Clock(dut.clk, period_ns, unit="ns").start())
    dut.rst_n.value = 0
    dut.key.value = int.from_bytes(KEY, "big")
    dut.epoch.value = EPOCH
    dut.tvalid_s.value = 0
    dut.tdata_s.value = 0
    dut.tkeep_s.value = 0
    dut.tlast_s.value = 0
    dut.tuser_s.value = 0
    dut.tready_m.value = 1
    dut.tready_c.value = 1
    for _ in range(5):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    for _ in range(2):
        await RisingEdge(dut.clk)


async def forward(dut, pkts, expected=None, throttle=0.0, rng=None):
    """Drive packets in, expect `expected` (default: all of them) on the
    packet port plus a counter-complete cert trail."""
    if expected is None:
        expected = pkts

    async def send():
        for p in pkts:
            await drive_pkt(dut, p)
    tx = cocotb.start_soon(send())
    rx = cocotb.start_soon(recv_axis(dut, "m", len(expected),
                                     throttle=throttle, rng=rng))
    rc = cocotb.start_soon(recv_certs_until(dut, expected, len(expected)))
    await Combine(tx, rx, rc)
    for i, ((g, l), exp) in enumerate(zip(rx.result(), expected)):
        assert g == exp, (f"packet {i}: len {len(g)} vs {len(exp)}\n"
                          f"  got {g[:32].hex()}\n  exp {exp[:32].hex()}")
        assert l == len(exp), f"packet {i}: tuser@beat0 {l} != {len(exp)}"


# ---------------------------------------------------------------------- tests

@cocotb.test()
async def test_min_packet(dut):
    """Minimum canonical packet (1-byte payload) passes and is attested."""
    await reset(dut)
    await forward(dut, [packet_for(1, 1)])


@cocotb.test()
async def test_sizes(dut):
    """Payload sizes covering padding, alignment, and the 1500-byte maximum."""
    await reset(dut)
    sizes = [1, 2, 3, 4, 33, 34, 35, 36, 100, 487, 1488]
    await forward(dut, [packet_for(1 + i, s) for i, s in enumerate(sizes)])


@cocotb.test()
async def test_multi_batch(dut):
    """Packets spread across batch periods come out in order with contiguous
    cert counters (i, count) across the batches."""
    await reset(dut)
    pkts = [packet_for(i, 50 + i) for i in range(1, 7)]

    async def send():
        for p in pkts[:3]:
            await drive_pkt(dut, p)
        for _ in range(BATCH_CYCLES):
            await RisingEdge(dut.clk)
        for p in pkts[3:]:
            await drive_pkt(dut, p)

    tx = cocotb.start_soon(send())
    rx = cocotb.start_soon(recv_axis(dut, "m", len(pkts)))
    rc = cocotb.start_soon(recv_certs_until(dut, pkts, len(pkts)))
    await Combine(tx, rx, rc)
    assert [p for p, _ in rx.result()] == pkts


@cocotb.test()
async def test_backpressure(dut):
    """Both output ports throttle tready; everything reflows without loss."""
    await reset(dut)
    await forward(dut, [packet_for(1 + i, 70 + 17 * i) for i in range(6)],
                  throttle=0.4, rng=random.Random(11))


@cocotb.test()
async def test_bad_packets_dropped(dut):
    """Header failures, payload-length lies, and upstream truncation all
    vanish — and never enter the batch digest; an interleaved good packet
    still passes and is attested as packet #1."""
    await reset(dut)
    bad = [
        # header fail (new ID with pkt_idx != 0): suppressed by canon_proc
        (canonical_packet(INF | 1, 7, 40, seed=1), False),
        # pld_len lies about the payload: dropped at the batch buffer
        (canonical_packet(INF | 2, 0, 99, payload_len=40, seed=2), False),
        # upstream (eth_deframe) truncation flag: propagates to the drop
        (canonical_packet(INF | 3, 0, 40, seed=3), True),
    ]
    good = packet_for(4, 60)

    async def send():
        for p, trunc in bad:
            await drive_pkt(dut, p, trunc=trunc)
        await drive_pkt(dut, good)
    tx = cocotb.start_soon(send())
    rx = cocotb.start_soon(recv_axis(dut, "m", 1))
    rc = cocotb.start_soon(recv_certs_until(dut, [good], 1))
    await Combine(tx, rx, rc)
    assert rx.result()[0] == (good, len(good))
    await expect_quiet(dut)
