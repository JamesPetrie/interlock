"""cocotb testbench for traffic_commit.sv — the commitment / attestation block.

Drives the drain-side AXI-Stream (packets with len @ beat #0, swap flag @
tlast) and checks two things against a Python reference:

  * the master data port re-emits every packet byte-exactly (verbatim
    pass-through, len @ beat #0 preserved);
  * the certificate port emits one frame every BUCKETS_PER_SEC batches (= one
    "second" of buckets), carrying
        m   = (version, interlock_id, bucket_start, num_buckets, direction,
               overall, nonce)
        tau = HMAC_k(m)
    where the hierarchy is
        H(packet) = SHA256(hdr || SHA256(ciphertext))    per packet
        record    = len || H(packet)                     per packet
        bucket    = SHA256(record_1 || ... || record_k)  per batch (= one bucket)
        overall   = SHA256(bucket_1 || ... || bucket_N)  over this second's buckets
    len = the bytes the block processed (2 B BE, the true emitted length), hdr =
    the first HDR_BYTES of the packet, ciphertext = the rest (a packet shorter
    than HDR_BYTES has an empty ciphertext -> SHA256("")).

Cross-second completeness rides on the bucket numbering: bucket_start advances
by num_buckets (= BUCKETS_PER_SEC) per certificate.

VERSION / INTERLOCK_ID / DIRECTION use the module defaults; key and nonce are
driven on the ports. BUCKETS_PER_SEC is overridden small in
the Makefile.
"""
import hashlib
import hmac as pyhmac
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, Combine

KEY = bytes.fromhex(
    "0f0e0d0c0b0a09080706050403020100"
    "1f1e1d1c1b1a19181716151413121110"
)
NONCE = bytes.fromhex("0123456789abcdeffedcba9876543210")   # 128-bit verifier nonce
VERSION = 1                          # module default
INTERLOCK_ID = 0                     # module default
DIRECTION = 0                        # module default (req/ingress)
HDR_BYTES = 12                       # header split (canon_pkg::CANON_RSP_HDR_BYTES)
BUCKETS_PER_SEC = 3                  # Makefile override
M_BYTES = 72                         # ver4 id4 bstart8 nbuck4 dir4 overall32 nonce16
FRAME_BYTES = HDR_BYTES + M_BYTES + 32   # reserved hdr + m + tau = 116


def packet_hash(pkt: bytes) -> bytes:
    """H(packet) = SHA256(header ‖ SHA256(ciphertext)); header = first HDR_BYTES,
    ciphertext = rest (empty -> SHA256(""))."""
    return hashlib.sha256(pkt[:HDR_BYTES] + hashlib.sha256(pkt[HDR_BYTES:]).digest()).digest()


def record(pkt: bytes) -> bytes:
    """Bucket-hash leaf: length (2 B BE) ‖ H(packet). length = total bytes the
    block processed (= len(pkt), the true emitted length); the header is hashed
    into H(packet), not carried cleartext."""
    return len(pkt).to_bytes(2, "big") + packet_hash(pkt)


def bucket_hash(batch_pkts) -> bytes:
    return hashlib.sha256(b"".join(record(p) for p in batch_pkts)).digest()


def overall_hash(buckets) -> bytes:
    return hashlib.sha256(b"".join(buckets)).digest()


def build_m(bucket_start: int, overall: bytes) -> bytes:
    return (VERSION.to_bytes(4, "big")
            + INTERLOCK_ID.to_bytes(4, "big")
            + bucket_start.to_bytes(8, "big")
            + BUCKETS_PER_SEC.to_bytes(4, "big")
            + DIRECTION.to_bytes(4, "big")
            + overall
            + NONCE)


def rand_bytes(n, seed):
    rng = random.Random(seed)
    return bytes(rng.randint(0, 255) for _ in range(n))


def pack_axis(data: bytes, swap: bool):
    """bytes -> [(tdata, tkeep, tlast, tuser)]; len @ beat0, swap (bit0) @ tlast."""
    assert len(data) > 4, "packets span >= 2 beats so len/swap never collide"
    n = (len(data) + 3) // 4
    beats = []
    for w in range(n):
        chunk = data[4 * w: 4 * w + 4]
        d = keep = 0
        for k, b in enumerate(chunk):
            d |= b << (8 * k)
            keep |= 1 << k
        last = int(w == n - 1)
        user = len(data) if w == 0 else (1 if (last and swap) else 0)
        beats.append((d, keep, last, user))
    return beats


async def drive_pkt(dut, data: bytes, swap: bool):
    for d, keep, last, user in pack_axis(data, swap):
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
    dut.tkeep_s.value = 0
    dut.tuser_s.value = 0


async def drive_stream(dut, pkts, gap=0):
    for data, swap in pkts:
        await drive_pkt(dut, data, swap)
        for _ in range(gap):
            await RisingEdge(dut.clk)


async def drive_continuous(dut, pkts):
    """Drive every packet's beats back-to-back without dropping tvalid_s — the
    no-gap stream a real buffer drain produces across packet boundaries."""
    beats = [b for data, swap in pkts for b in pack_axis(data, swap)]
    i = 0
    while i < len(beats):
        d, keep, last, user = beats[i]
        dut.tvalid_s.value = 1
        dut.tdata_s.value = d
        dut.tkeep_s.value = keep
        dut.tlast_s.value = last
        dut.tuser_s.value = user
        await ReadOnly()
        ready = int(dut.tready_s.value)
        await RisingEdge(dut.clk)
        if ready:
            i += 1
    dut.tvalid_s.value = 0
    dut.tlast_s.value = 0
    dut.tkeep_s.value = 0
    dut.tuser_s.value = 0


async def recv_master(dut, n_packets, throttle=0.0, rng=None):
    """Collect n packets from the data port: (bytes, len@beat0)."""
    rng = rng or random.Random(0)
    pkts, cur, tuser0 = [], bytearray(), None
    while len(pkts) < n_packets:
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
    dut.tready_m.value = 1
    return pkts


async def recv_certs(dut, n_certs, throttle=0.0, rng=None):
    """Collect n certificate frames: (bytes, len@beat0)."""
    rng = rng or random.Random(0)
    certs, cur, tuser0 = [], bytearray(), None
    while len(certs) < n_certs:
        dut.tready_c.value = 0 if rng.random() < throttle else 1
        await ReadOnly()
        if int(dut.tvalid_c.value) and int(dut.tready_c.value):
            d, keep = int(dut.tdata_c.value), int(dut.tkeep_c.value)
            if tuser0 is None:
                tuser0 = int(dut.tuser_c.value)
            for k in range(4):
                if (keep >> k) & 1:
                    cur.append((d >> (8 * k)) & 0xFF)
            if int(dut.tlast_c.value):
                certs.append((bytes(cur), tuser0))
                cur, tuser0 = bytearray(), None
        await RisingEdge(dut.clk)
    dut.tready_c.value = 1
    return certs


def check_cert(cert, exp_bucket_start, exp_overall):
    """Parse one cert frame and verify the reserved header, m fields + tau."""
    data, length = cert
    assert length == FRAME_BYTES, f"cert len@beat0 {length} != {FRAME_BYTES}"
    assert len(data) == FRAME_BYTES, f"cert is {len(data)} bytes != {FRAME_BYTES}"
    hdr, rest = data[:HDR_BYTES], data[HDR_BYTES:]
    assert hdr == bytes(HDR_BYTES), f"reserved header not all-zero: {hdr.hex()}"
    m, tau = rest[:M_BYTES], rest[M_BYTES:]

    version = int.from_bytes(m[0:4], "big")
    interlock_id = int.from_bytes(m[4:8], "big")
    bucket_start = int.from_bytes(m[8:16], "big")
    num_buckets = int.from_bytes(m[16:20], "big")
    direction = int.from_bytes(m[20:24], "big")
    overall = m[24:56]
    nonce = m[56:72]

    assert version == VERSION, f"cert version {version} != {VERSION}"
    assert interlock_id == INTERLOCK_ID, f"cert interlock_id {interlock_id} != {INTERLOCK_ID}"
    assert bucket_start == exp_bucket_start, f"cert bucket_start {bucket_start} != {exp_bucket_start}"
    assert num_buckets == BUCKETS_PER_SEC, f"cert num_buckets {num_buckets} != {BUCKETS_PER_SEC}"
    assert direction == DIRECTION, f"cert direction {direction} != {DIRECTION}"
    assert overall == exp_overall, f"cert overall {overall.hex()} != {exp_overall.hex()}"
    assert nonce == NONCE, f"cert nonce {nonce.hex()} != {NONCE.hex()}"

    exp_m = build_m(exp_bucket_start, exp_overall)
    assert m == exp_m, f"cert m {m.hex()} != {exp_m.hex()}"
    exp_tau = pyhmac.new(KEY, m, hashlib.sha256).digest()
    assert tau == exp_tau, f"cert tau {tau.hex()} != {exp_tau.hex()}"


def expected(pkts):
    """Reference: return (master_pkts, certs=(bucket_start, overall)).

    Each swap-flagged packet closes a batch (= one bucket); every
    BUCKETS_PER_SEC buckets produce one certificate.
    """
    master, buckets, batch = [], [], []
    for data, swap in pkts:
        master.append(data)
        batch.append(data)
        if swap:
            buckets.append(bucket_hash(batch))
            batch = []
    certs = []
    for c in range(len(buckets) // BUCKETS_PER_SEC):
        grp = buckets[c * BUCKETS_PER_SEC:(c + 1) * BUCKETS_PER_SEC]
        certs.append((c * BUCKETS_PER_SEC, overall_hash(grp)))
    return master, certs


async def reset(dut):
    cocotb.start_soon(Clock(dut.clk, 8, unit="ns").start())
    dut.rst_n.value = 0
    dut.key.value = int.from_bytes(KEY, "big")
    dut.nonce.value = int.from_bytes(NONCE, "big")
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


def make_batches(n_batches, rng, lo=5, hi=60, seed0=0):
    """n_batches batches of 1..4 packets each; last packet of a batch swaps."""
    pkts = []
    for b in range(n_batches):
        k = rng.randint(1, 4)
        for j in range(k):
            n = rng.randint(lo, hi)
            pkts.append((rand_bytes(n, seed0 + 17 * b + j), j == k - 1))
    return pkts


async def run_case(dut, pkts, **rxargs):
    exp_master, exp_certs = expected(pkts)
    if rxargs.pop("continuous", False):
        tx = cocotb.start_soon(drive_continuous(dut, pkts))
    else:
        tx = cocotb.start_soon(drive_stream(dut, pkts, gap=rxargs.pop("gap", 0)))
    rm = cocotb.start_soon(recv_master(dut, len(exp_master),
                                       throttle=rxargs.get("m_throttle", 0.0),
                                       rng=random.Random(1)))
    rc = cocotb.start_soon(recv_certs(dut, len(exp_certs),
                                      throttle=rxargs.get("c_throttle", 0.0),
                                      rng=random.Random(2)))
    await Combine(tx, rm, rc)
    got_master = rm.result()
    got_certs = rc.result()

    assert len(got_master) == len(exp_master)
    for k, ((g, l), e) in enumerate(zip(got_master, exp_master)):
        assert g == e, f"pkt {k} passthrough mismatch: {g.hex()} != {e.hex()}"
        assert l == len(e), f"pkt {k} len@beat0 {l} != {len(e)}"
    assert len(got_certs) == len(exp_certs)
    for cert, (exp_bs, exp_ov) in zip(got_certs, exp_certs):
        check_cert(cert, exp_bs, exp_ov)


# --------------------------------------------------------------------------

@cocotb.test()
async def test_single_second(dut):
    """BUCKETS_PER_SEC batches -> exactly one certificate over their buckets."""
    await reset(dut)
    rng = random.Random(42)
    pkts = make_batches(BUCKETS_PER_SEC, rng, seed0=10)
    await run_case(dut, pkts)


@cocotb.test()
async def test_header_ciphertext_split(dut):
    """Packet sizes around the HDR_BYTES boundary: below it (empty ciphertext),
    at it, and above it (real ciphertext) — exercises both halves of the
    record hash and partial header tails."""
    await reset(dut)
    sizes = [5, 8, 11, 12, 13, 16, 23, 40, 41, 64, 65, 100]
    # split the 12 packets into 3 buckets of 4 (one second)
    pkts = []
    for i, n in enumerate(sizes):
        pkts.append((rand_bytes(n, 100 + n), (i % 4) == 3))
    await run_case(dut, pkts)


@cocotb.test()
async def test_multi_second(dut):
    """Two seconds (2*BUCKETS_PER_SEC batches) -> two certs; bucket_start steps
    by num_buckets."""
    await reset(dut)
    rng = random.Random(7)
    pkts = make_batches(2 * BUCKETS_PER_SEC, rng, seed0=200)
    await run_case(dut, pkts, gap=3)


@cocotb.test()
async def test_backpressure(dut):
    """Throttle both the data and the certificate sinks across two seconds."""
    await reset(dut)
    rng = random.Random(13)
    pkts = make_batches(2 * BUCKETS_PER_SEC, rng, seed0=300)
    await run_case(dut, pkts, m_throttle=0.3, c_throttle=0.3, gap=2)


@cocotb.test()
async def test_single_packet_buckets(dut):
    """Every batch is one packet -> one bucket each; BUCKETS_PER_SEC of them
    make a cert. Exercises the single-packet-batch record path."""
    await reset(dut)
    pkts = [(rand_bytes(n, 400 + n), True) for n in [16, 24, 40, 48, 56, 72]]
    await run_case(dut, pkts)            # 6 buckets -> 2 certs


@cocotb.test()
async def test_continuous(dut):
    """Packets driven back-to-back (tvalid_s never drops between them) across
    two seconds — the packet-boundary handling must not need a gap."""
    await reset(dut)
    rng = random.Random(99)
    pkts = make_batches(2 * BUCKETS_PER_SEC, rng, seed0=500)
    await run_case(dut, pkts, continuous=True)


@cocotb.test()
async def test_preempt_termination(dut):
    """A drain preemption cuts a batch's last packet: data beats without tlast,
    then an empty termination beat (tkeep=0, tlast, swap). The cut packet's
    emitted bytes are committed, the empty beat adds nothing. Three batches
    (one of them cut) make one cert."""
    await reset(dut)
    p1, p2 = rand_bytes(20, 600), rand_bytes(13, 601)
    cut = rand_bytes(16, 602)            # 4 full beats emitted, then cut
    b2 = [rand_bytes(24, 610), rand_bytes(30, 611)]
    b3 = [rand_bytes(40, 620)]

    async def send_cut_batch():
        await drive_pkt(dut, p1, swap=False)
        await drive_pkt(dut, p2, swap=False)
        beats = [b for b in pack_axis(cut + b"\x00" * 4, swap=False)][:-1]
        beats[0] = (beats[0][0], beats[0][1], 0, 40)   # claimed len 40
        beats.append((0, 0, 1, 1))                     # termination beat (swap)
        for d, keep, last, user in beats:
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
        dut.tkeep_s.value = 0
        dut.tuser_s.value = 0

    # batch 1 (cut), batch 2 (2 pkts), batch 3 (1 pkt) -> one second -> one cert
    batch1 = [p1, p2, cut]
    async def send():
        await send_cut_batch()
        await drive_pkt(dut, b2[0], swap=False)
        await drive_pkt(dut, b2[1], swap=True)
        await drive_pkt(dut, b3[0], swap=True)

    n_master = len(batch1) + len(b2) + len(b3)
    tx = cocotb.start_soon(send())
    rm = cocotb.start_soon(recv_master(dut, n_master))
    rc = cocotb.start_soon(recv_certs(dut, 1))
    await Combine(tx, rm, rc)
    got_m, got_c = rm.result(), rc.result()
    assert [g for g, _ in got_m] == [p1, p2, cut] + b2 + b3
    assert got_m[2][1] == 40, "cut packet keeps its claimed len @ beat #0"

    exp_overall = overall_hash([bucket_hash(batch1), bucket_hash(b2), bucket_hash(b3)])
    check_cert(got_c[0], exp_bucket_start=0, exp_overall=exp_overall)
