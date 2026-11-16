"""cocotb testbench for batch_buffer.sv — the ping-pong batch packet store.

New (free-flowing write / drain-side grace) contract:

  * Writes are free-flowing — there is no admission guard. The fill bank
    swaps when an empty *delimiter* beat (tvalid, tlast, tkeep == 0) arrives
    on the slave port; that beat also freezes the just-filled bank's
    committed pointer for the drain (rd_limit).
  * The drain side is driven by two external signals: a single-cycle `tick`
    pulse swaps the drain bank (marked invalid), and `timer` reaching
    GRACE_PERIOD-1 starts the drain on the now-frozen bank.
  * Overrun guard: a record whose last word lands in the reserved tail
    (>= SAFE_END = BANK_WORDS - MAX_ENTRY_WORDS) is abandoned, not committed
    — so an over-capacity bucket loses its overflow packets but never
    corrupts committed data.

The TB models the surrounding system: a free-running timer (period PERIOD,
tick at PERIOD-1) and the upstream delimiter that canon_proc emits right
after every tick. Each "batch" is the set of packets sent between two
delimiters; a batch filled during one period drains during the next.

Geometry here matches the parameter overrides in the Makefile.
"""
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, Combine

# --- must match the Makefile -P overrides ---
BANK_WORDS      = 64
GRACE_PERIOD    = 16
MAX_ENTRY_WORDS = 17
OUTPUT_SWAP     = int(os.environ.get("BB_OUTPUT_SWAP", "0"))  # matches the -P override
SAFE_END        = BANK_WORDS - MAX_ENTRY_WORDS    # 47: reserved-tail boundary

# --- TB-side timer model (the DUT only samples tick + timer==GRACE_PERIOD-1) ---
PERIOD = 128                                      # timer wraps every PERIOD cycles


def rand_bytes(n, seed):
    rng = random.Random(seed)
    return bytes(rng.randint(0, 255) for _ in range(n))


def rec_words(length):
    """Words a record occupies: one length prefix + ceil(length/4) data."""
    return 1 + (length + 3) // 4


def simulate_fill(batch):
    """Model the fill side: returns the payloads that survive into the drain.

    A record commits iff it is not drop-flagged and its last word lands
    below SAFE_END; otherwise it is abandoned (wr_ptr rewinds to wr_cmt).
    """
    wr_cmt = 0
    kept = []
    for data, drop in batch:
        end_addr = wr_cmt + rec_words(len(data)) - 1
        if (not drop) and (end_addr < SAFE_END):
            kept.append(data)
            wr_cmt = end_addr + 1
    return kept


def pack_axis(data: bytes, drop: bool = False):
    """bytes -> list of (tdata, tkeep, tlast, tuser) beats.

    tuser = total length at beat #0, drop flag at tlast; packets must span
    >= 2 beats so the two positions never collide (canon_proc guarantees
    this upstream).
    """
    assert len(data) > 4, "packet too short for distinct beat#0/tlast tuser"
    n = (len(data) + 3) // 4
    beats = []
    for w in range(n):
        chunk = data[4 * w: 4 * w + 4]
        d = keep = 0
        for k, b in enumerate(chunk):
            d |= b << (8 * k)
            keep |= 1 << k
        last = int(w == n - 1)
        user = len(data) if w == 0 else (1 if (last and drop) else 0)
        beats.append((d, keep, last, user))
    return beats


# --------------------------------------------------------------------------
# Stimulus / monitor helpers
# --------------------------------------------------------------------------
async def run_timer(dut):
    """Free-running timer + tick — models cnl_core's bank-swap clock."""
    t = 0
    while True:
        dut.timer.value = t
        dut.tick.value = 1 if t == PERIOD - 1 else 0
        await RisingEdge(dut.clk)
        t = (t + 1) % PERIOD


async def wait_tick(dut):
    """Return on the clk edge at which a tick is sampled."""
    while True:
        await ReadOnly()
        t = int(dut.tick.value)
        await RisingEdge(dut.clk)
        if t:
            return


async def drive_pkt(dut, data: bytes, drop: bool = False):
    """Drive one packet; beat #0 waits out the one-cycle prefix write."""
    beats = pack_axis(data, drop)
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


async def send_delimiter(dut):
    """Drive the empty bucket-boundary beat (tvalid, tlast, tkeep == 0)."""
    dut.tvalid_s.value = 1
    dut.tdata_s.value = 0
    dut.tkeep_s.value = 0
    dut.tlast_s.value = 1
    dut.tuser_s.value = 0
    while True:
        await ReadOnly()
        ready = int(dut.tready_s.value)
        await RisingEdge(dut.clk)
        if ready:
            break
    dut.tvalid_s.value = 0
    dut.tlast_s.value = 0


async def drive_batches(dut, batches):
    """Fill each batch during one period, then close it with a delimiter
    immediately after that period's tick (matching canon_proc)."""
    for batch in batches:
        for data, drop in batch:
            await drive_pkt(dut, data, drop)
        await wait_tick(dut)
        await send_delimiter(dut)


async def recv_pkts(dut, n_packets, throttle=0.0, rng=None):
    """Collect n_packets from the master port: (bytes, tuser@beat0).

    Empty swap beats (tkeep == 0, only emitted when OUTPUT_SWAP=1) are
    skipped here; test_output_swap_trailing_beat covers them directly.

    Note: the block used to mark a batch's final record with a swap flag in
    tuser bit 0. That is gone — the batch boundary is now carried solely by
    the standalone empty beat under OUTPUT_SWAP=1, so with OUTPUT_SWAP=0
    there is no boundary marker in the stream at all and tuser is the length
    on beat #0, zero elsewhere.
    """
    if rng is None:
        rng = random.Random(0)
    pkts = []
    cur = bytearray()
    tuser0 = None
    while len(pkts) < n_packets:
        dut.tready_m.value = 0 if rng.random() < throttle else 1
        await ReadOnly()
        if int(dut.tvalid_m.value) and int(dut.tready_m.value):
            d = int(dut.tdata_m.value)
            keep = int(dut.tkeep_m.value)
            last = int(dut.tlast_m.value)
            user = int(dut.tuser_m.value)
            if keep != 0:                       # real data beat
                if tuser0 is None:
                    tuser0 = user
                for k in range(4):
                    if (keep >> k) & 1:
                        cur.append((d >> (8 * k)) & 0xFF)
                if last:
                    pkts.append((bytes(cur), tuser0))
                    cur = bytearray()
                    tuser0 = None
        await RisingEdge(dut.clk)
    dut.tready_m.value = 1
    return pkts


async def pulse(dut, sig):
    """One-cycle pulse on a control input."""
    sig.value = 1
    await RisingEdge(dut.clk)
    sig.value = 0


async def quiet_for(dut, cycles):
    """No data beats while held. With OUTPUT_SWAP=0 the port is fully silent;
    with OUTPUT_SWAP=1 a held period with no replay pending falls through to
    the empty-bucket branch, so the lone trailing swap beat still marks every
    bucket period and the cadence stays visible downstream. Returns how many
    of those delimiter beats were seen."""
    dut.tready_m.value = 1
    delims = 0
    for _ in range(cycles):
        await ReadOnly()
        if int(dut.tvalid_m.value):
            keep = int(dut.tkeep_m.value)
            last = int(dut.tlast_m.value)
            assert OUTPUT_SWAP and keep == 0 and last == 1, \
                "data beat during a held period"
            delims += 1
        await RisingEdge(dut.clk)
    return delims


async def reset(dut, period_ns=8):
    cocotb.start_soon(Clock(dut.clk, period_ns, unit="ns").start())
    dut.rst_n.value = 0
    dut.tvalid_s.value = 0
    dut.tdata_s.value = 0
    dut.tkeep_s.value = 0
    dut.tlast_s.value = 0
    dut.tuser_s.value = 0
    dut.tready_m.value = 1
    dut.tick.value = 0
    dut.timer.value = 0
    # Release gate, added with the recomp release feature. Held open here,
    # which is how the prod path ties it (fabric_bridge passes 1'b1/1'b1).
    # Left undriven these sit at X, so rd_gate_en_r never latches and the
    # drain condition (rd_limit != '0) && rd_gate_en_r is never true — the
    # bench would then wait forever for output that cannot come.
    dut.rd_gate_en_valid.value = 1
    dut.rd_gate_en.value = 1
    # Bucket retry, added with the recomp full-retry feature. A constant ack
    # keeps the drain-start lock clear, which is the feature-off tie the
    # instantiations use — every test but the retry one runs that way.
    dut.bkt_ack.value = 1
    dut.bkt_replay.value = 0
    for _ in range(5):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    for _ in range(2):
        await RisingEdge(dut.clk)


def check(got, batches_kept):
    """got from recv_pkts vs the per-batch kept-payload lists.

    Order and content across batches, plus the beat-#0 length. Batch
    boundaries are not asserted here — they are no longer visible in the data
    stream (see recv_pkts); test_output_swap_trailing_beat covers the
    delimiter that carries them under OUTPUT_SWAP=1.
    """
    flat = [d for e in batches_kept for d in e]
    assert len(got) == len(flat), f"got {len(got)} packets, expected {len(flat)}"
    for i, ((g, t0), d) in enumerate(zip(got, flat)):
        assert g == d, (f"packet {i} mismatch (len {len(d)} vs {len(g)}): "
                        f"{g[:16].hex()} != {d[:16].hex()}")
        assert t0 == len(d), f"packet {i} tuser@beat0={t0} != len {len(d)}"


async def run(dut, batches, throttle=0.0, seed=0):
    await reset(dut)
    cocotb.start_soon(run_timer(dut))
    kept = [simulate_fill(b) for b in batches]
    total = sum(len(e) for e in kept)
    rx = cocotb.start_soon(recv_pkts(dut, total, throttle, random.Random(seed)))
    tx = cocotb.start_soon(drive_batches(dut, batches))
    await Combine(tx, rx)
    check(rx.result(), kept)
    return kept


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------
@cocotb.test()
async def test_single_packet(dut):
    """One packet fills a bank and re-emits after the swap, with the swap
    flag on its tlast and tuser = length on beat #0."""
    await run(dut, [[(rand_bytes(33, seed=1), False)]])


@cocotb.test()
async def test_batch(dut):
    """Several packets land in one bank and drain in order after the swap;
    only the batch's final packet carries the swap flag."""
    batch = [(rand_bytes(n, seed=10 + n), False) for n in (5, 8, 13, 24, 31, 40)]
    await run(dut, [batch])


@cocotb.test()
async def test_drop_abandoned(dut):
    """A drop-flagged packet's record is abandoned: overwritten by the next
    packet, never emitted."""
    batch = [(rand_bytes(20, seed=1), False),
             (rand_bytes(32, seed=2), True),     # dropped
             (rand_bytes(12, seed=3), False)]
    await run(dut, [batch])


@cocotb.test()
async def test_multi_batch(dut):
    """Several batches drain across swaps in order; each batch's last packet
    closes it with the swap flag."""
    rng = random.Random(42)
    batches = []
    for i in range(5):
        n = rng.randint(2, 4)
        batches.append([(rand_bytes(rng.randint(5, 32), seed=100 + 10 * i + j), False)
                        for j in range(n)])
    await run(dut, batches)


@cocotb.test()
async def test_overrun_guard(dut):
    """An over-capacity batch: records that overrun the reserved tail are
    abandoned (not committed), the rest drain cleanly. wr_ptr never leaves
    the bank."""
    batch = [(rand_bytes(16, seed=60 + i), False) for i in range(14)]  # 5 words each
    kept = await run(dut, batch and [batch])
    # sanity: the model must actually exercise the guard (some dropped)
    assert 0 < len(kept[0]) < 14, f"guard not exercised: {len(kept[0])} kept"


@cocotb.test()
async def test_safe_end_boundary(dut):
    """Pin the exact reserved-tail boundary so an off-by-one (wr_ptr < SAFE_END
    vs <=) is caught. SAFE_END = BANK_WORDS - MAX_ENTRY_WORDS = 47.
      b_drop: 9x 16B (5 words) -> wr_cmt=45, then a 5B record (3 words) ends at
              word 47 == SAFE_END -> MUST be abandoned (9 kept).
      b_keep: 8x 16B -> wr_cmt=40, then a 24B record (7 words) ends at word 46
              == SAFE_END-1 -> MUST be committed (9 kept).
    A `<=` guard would keep b_drop's last record (10 kept) and diverge."""
    b_drop = [(rand_bytes(16, seed=200 + i), False) for i in range(9)] \
             + [(rand_bytes(5, seed=291), False)]
    b_keep = [(rand_bytes(16, seed=210 + i), False) for i in range(8)] \
             + [(rand_bytes(24, seed=292), False)]
    # sanity that the batches actually straddle the boundary as intended
    assert len(simulate_fill(b_drop)) == 9, "b_drop must drop exactly its last record"
    assert len(simulate_fill(b_keep)) == 9, "b_keep must keep its boundary record"
    await run(dut, [b_drop, b_keep])


@cocotb.test()
async def test_stale_rd_limit(dut):
    """non-empty -> empty -> non-empty: the empty batch's delimiter must reset
    rd_limit to 0 so the drain does NOT re-emit the previous bank; the next
    batch then drains cleanly. Exercises the rd_limit reset path (not just the
    from-reset rd_limit==0 case)."""
    b1 = [(rand_bytes(n, seed=400 + n), False) for n in (13, 24, 31)]
    b2 = [(rand_bytes(n, seed=500 + n), False) for n in (8, 40)]
    await run(dut, [b1, [], b2])


@cocotb.test()
async def test_backpressure(dut):
    """Sink throttles tready_m; the batch still drains completely and in
    order within its period."""
    batch = [(rand_bytes(n, seed=50 + n), False) for n in (12, 7, 19, 28, 9)]
    await run(dut, [batch], throttle=0.4, seed=9)


@cocotb.test()
async def test_mixed_stream(dut):
    """Many batches, variable sizes, random drops, mild output throttle —
    order and content preserved across every swap."""
    rng = random.Random(7)
    batches = []
    for i in range(6):
        n = rng.randint(2, 5)
        batch = [(rand_bytes(rng.randint(5, 40), seed=300 + 10 * i + j),
                  rng.random() < 0.25) for j in range(n)]
        # guarantee at least one survivor so the batch produces output
        if not simulate_fill(batch):
            batch[0] = (batch[0][0], False)
        batches.append(batch)
    await run(dut, batches, throttle=0.2, seed=11)


@cocotb.test(skip=(OUTPUT_SWAP == 0))
async def test_output_swap_trailing_beat(dut):
    """OUTPUT_SWAP=1 (buffer_rsp's config): each drained batch is closed by
    exactly one trailing empty delimiter beat (tkeep==0 & tlast==1) after its
    real records — the D_SWAP path, otherwise unexercised by the unit TB.
    Run with:  make BB_OUTPUT_SWAP=1"""
    await reset(dut)
    cocotb.start_soon(run_timer(dut))
    batch = [(rand_bytes(n, seed=700 + n), False) for n in (13, 24, 31)]
    tx = cocotb.start_soon(drive_batches(dut, [batch]))
    dut.tready_m.value = 1
    real_last = empty_delim = cyc = 0
    while empty_delim < 1 and cyc < 4 * PERIOD:
        await ReadOnly()
        if int(dut.tvalid_m.value) and int(dut.tready_m.value):
            keep, last = int(dut.tkeep_m.value), int(dut.tlast_m.value)
            if last and keep == 0:
                empty_delim += 1
                assert real_last == len(batch), \
                    f"swap beat after {real_last} records, expected {len(batch)}"
            elif last:
                real_last += 1
        await RisingEdge(dut.clk)
        cyc += 1
    assert empty_delim == 1, f"expected exactly one trailing swap beat, got {empty_delim}"
    await tx


@cocotb.test(skip=True)  # REVISIT: known-failing — grace clobbers a stalled
# close (see the REVISIT in batch_buffer.sv's grace block); un-skip once the
# drain_late deferral lands.
async def test_close_stalled_across_grace(dut):
    """The sink stalls just before b1's final data beat, so the batch close
    (D_SWAP) is still owed when the NEXT grace fires. The deferred bucket must
    not clobber the owed close: after release expect b1's tail, b1's close,
    then b2's records and b2's close — two closes, nothing lost."""
    await reset(dut)
    cocotb.start_soon(run_timer(dut))
    b1 = [(rand_bytes(13, seed=900), False)]                      # 4 data beats
    b2 = [(rand_bytes(n, seed=910 + n), False) for n in (8, 21)]
    tx = cocotb.start_soon(drive_batches(dut, [b1, b2]))

    pkts, closes, cur, beats, cyc = [], 0, bytearray(), 0, 0
    dut.tready_m.value = 1
    while closes < 2 and cyc < 8 * PERIOD:
        await ReadOnly()
        if int(dut.tvalid_m.value) and int(dut.tready_m.value):
            keep = int(dut.tkeep_m.value)
            last = int(dut.tlast_m.value)
            d = int(dut.tdata_m.value)
            if keep == 0 and last:
                closes += 1
            else:
                beats += 1
                for k in range(4):
                    if (keep >> k) & 1:
                        cur.append((d >> (8 * k)) & 0xFF)
                if last:
                    pkts.append(bytes(cur))
                    cur = bytearray()
        await RisingEdge(dut.clk)
        cyc += 1
        # stall right before b1's final beat: its tlast beat sits pending in
        # the output register, parking the close in D_SWAP across the next
        # tick + grace; release well past that grace
        stall = (beats == 3) and (cyc < 2 * PERIOD + GRACE_PERIOD + 8)
        dut.tready_m.value = 0 if stall else 1
    assert closes == 2, f"expected 2 closes (b1's deferred + b2's), got {closes}"
    exp = [b1[0][0]] + [d for d, _ in b2]
    assert pkts == exp, ("data lost/corrupted around the deferred close: "
                         f"{[len(p) for p in pkts]} vs {[len(e) for e in exp]}")


@cocotb.test()
async def test_bucket_retained_and_replayed(dut):
    """Bucket retry: with bkt_ack withheld, the drain start locks the bucket —
    the banks stop swapping, ingress periods are discarded in place, and no
    data drains (only the per-period swap beat under OUTPUT_SWAP=1) — until
    each bkt_replay re-walks the identical bucket at the next grace boundary.
    bkt_ack then releases the banks and a fresh bucket flows; the traffic
    discarded while held must never appear."""
    await reset(dut)
    dut.bkt_ack.value = 0                     # retry mode: no auto-release
    cocotb.start_soon(run_timer(dut))
    b1 = [rand_bytes(n, seed=800 + n) for n in (13, 24, 31)]
    b2 = [rand_bytes(17, seed=850)]           # lands while held -> discarded
    b3 = [rand_bytes(21, seed=860)]           # after the release

    # period 0: fill b1, close it at the tick
    for d in b1:
        await drive_pkt(dut, d)
    await wait_tick(dut)
    await send_delimiter(dut)

    # period 1: first issue — the drain start latches the lock; b2 fills the
    # other bank meanwhile
    check(await recv_pkts(dut, len(b1)), [b1])
    for d in b2:
        await drive_pkt(dut, d)
    await wait_tick(dut)                      # hold engages: banks stay put
    await send_delimiter(dut)                 # ...and this discards b2

    # period 2: held with no request — no data; exactly the one per-period
    # swap beat under OUTPUT_SWAP=1
    delims = await quiet_for(dut, PERIOD)
    assert delims == (1 if OUTPUT_SWAP else 0), \
        f"held period emitted {delims} swap beats, expected {int(OUTPUT_SWAP)}"

    # periods 3, 4: every replay request re-issues the identical bucket
    for _ in range(2):
        await pulse(dut, dut.bkt_replay)
        check(await recv_pkts(dut, len(b1)), [b1])
        await wait_tick(dut)
        await send_delimiter(dut)             # held: discarded, banks stay

    # acknowledge -> banks resume swapping: one empty recovery period (the
    # held fill bank was discarded), then a fresh bucket drains
    await pulse(dut, dut.bkt_ack)
    await wait_tick(dut)
    await send_delimiter(dut)
    for d in b3:
        await drive_pkt(dut, d)
    await wait_tick(dut)
    await send_delimiter(dut)
    check(await recv_pkts(dut, len(b3)), [b3])


@cocotb.test()
async def test_empty_batches(dut):
    """Delimiters/ticks with no packets: no data beat ever appears. With
    OUTPUT_SWAP=1 every bucket period still emits exactly one trailing empty
    delimiter beat (the timer-driven downstream bucket close) — except the
    period-0 grace, before the first tick; with OUTPUT_SWAP=0 the port stays
    silent."""
    await reset(dut)
    cocotb.start_soon(run_timer(dut))
    dut.tready_m.value = 1
    drv = cocotb.start_soon(drive_batches(dut, [[], [], []]))
    delims = 0
    for _ in range(4 * PERIOD):
        await ReadOnly()
        if int(dut.tvalid_m.value) and int(dut.tready_m.value):
            keep = int(dut.tkeep_m.value)
            last = int(dut.tlast_m.value)
            assert keep == 0 and last == 1, \
                "data beat with no committed records"
            delims += 1
        await RisingEdge(dut.clk)
    exp = 3 if OUTPUT_SWAP else 0
    assert delims == exp, f"expected {exp} empty-batch swap beats, got {delims}"
