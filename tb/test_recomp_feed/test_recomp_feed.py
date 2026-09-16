"""cocotb testbench for recomp_feed.sv — forwarding, token feeding, scoring.

Buckets on the packet port are delimited by the empty swap beat (tkeep = 0,
tlast) batch_buffer re-inserts; arming is positional. The first packet after
a swap beat (or after reset — the stream starts at a bucket boundary) is the
challenged response: forwarded in place as a header-only packet, sanitized
on the fly — PLD_LEN and RESERVED zeroed, ID and BUCKET passed through, the
payload stripped (captured, never forwarded). Everything behind it is
context, forwarded verbatim; the challenge is self-terminating on the
enclosure side (the last context packet's ID matches the challenged ID).
The closing swap beat is consumed (never forwarded) and arms the estimate
expectation, after which the payload tokens are fed back one beat at a
time, one per estimate the enclosure returns.

Estimates are (value, probability) word-pair frames: value words raw (tokens
verbatim, numeric values big-endian), probabilities big-endian custom floats
(recomp_pkg). Each frame is normalization-checked; the actual value's
probability (or the catch-all / max-entropy default) runs through log2_iter
and accumulates into U.

The TB drives real frames and checks the forwarded stream, the reveal order,
and the final U against an exact model: Fraction for normalization, a
bit-exact replica of the log2_iter truncating iteration for surprisal.
"""
import os
import random
import re
from fractions import Fraction
from pathlib import Path
from struct import pack

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, Combine

# canon_pkg geometry (matches canon_pkg.sv, ETH_LEN_MAX = 1500).
# TOK_BYTES must match the CANON_TOK_BYTES the DUT was compiled with
# (the Makefile derives it from canon_pkg.sv; env override for sweeps).
HDR_BYTES = 64          # CANON_RSP_HDR_BYTES (pld_len|bucket|id|reserved)
TOK_BYTES = int(os.environ.get("TOK_BYTES", "4"))   # CANON_TOK_BYTES
TOK_MAX   = (1500 - HDR_BYTES) // TOK_BYTES   # max payload tokens (full packet)

# recomp_pkg geometry
EXP_W   = 6
MANT_W  = 32 - EXP_W
EXP_MAX = (1 << EXP_W) - 1
SPACE_W = 8 * TOK_BYTES                       # catch-all value-space width

_PKG = Path(__file__).resolve().parents[2] / "gateware/src/src_hdl/recomp_pkg.sv"
FRAC_W = int(re.search(r"LOG2_FRAC_W\s*=\s*(\d+)", _PKG.read_text()).group(1))
GUARD_W = (FRAC_W - 1).bit_length() + 3       # mirrors log2_iter's $clog2()+3
XW = max(FRAC_W + GUARD_W, MANT_W)

P_DEFAULT = EXP_MAX << MANT_W                 # PROB_MIN: norm-fail / no-p charge
RSP_ID    = 0x0BAD_F00D_CAFE                  # response header ID
BKT_DIFF  = 0x00C0FFEE                        # reserved0 low word (timing act)


# --------------------------------------------------------------------------
# number-format models (exact)
# --------------------------------------------------------------------------
def prob_word(exp, mant):
    assert 0 <= exp <= EXP_MAX and 0 <= mant < (1 << MANT_W)
    return (exp << MANT_W) | mant


def prob_val(word):
    exp, mant = word >> MANT_W, word & ((1 << MANT_W) - 1)
    return Fraction((1 << MANT_W) + mant, 1 << (MANT_W + exp))


def model_log2(word):
    """Bit-exact replica of the log2_iter iteration; signed Q.FRAC_W int."""
    exp, mant = word >> MANT_W, word & ((1 << MANT_W) - 1)
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


def model_norm_fail(probs):
    """Mirror of norm_chk: sticky fail, catch-all (last) charged x 2^SPACE_W
    with an exponent below SPACE_W failing outright."""
    s, failed = Fraction(0), False
    for i, w in enumerate(probs):
        if failed:
            break
        if i == len(probs) - 1:                   # catch-all
            if (w >> MANT_W) < SPACE_W:
                return True
            s += prob_val(w) * (1 << SPACE_W)
        else:
            s += prob_val(w)
        failed = s > 1
    return failed


def model_frame_surprisal(pairs, act, tail=None):
    """Expected U contribution of one frame: -log2 of the effective p.

    pairs: [(val_bytes4, prob_word)]; act: 4-byte actual value; tail: a
    dangling tlast word (odd frame) — it acts as a bare catch-all: it
    normalizes x 2^SPACE_W and is the fallback probability.
    Mirrors the RTL: norm-fail -> PROB_MIN; otherwise the first entry whose
    value matches `act` wins and the frame's last word is the fallback
    (catch-all). len, time and token frames all score the same way.
    """
    probs = [p for _, p in pairs] + ([tail] if tail is not None else [])
    if model_norm_fail(probs):
        p_eff = P_DEFAULT
    else:
        p_eff = next((p for v, p in pairs if v == act), probs[-1])
    return -model_log2(p_eff)


# --------------------------------------------------------------------------
# packet / frame builders
# --------------------------------------------------------------------------
def rand_bytes(n, seed):
    rng = random.Random(seed)
    return bytes(rng.randint(0, 255) for _ in range(n))


def rsp_pkt(pld, bucket=0x1357, pkt_id=RSP_ID, bkt_diff=BKT_DIFF):
    """Canonical response packet: 64-byte header (pld_len|bucket|id|reserved)
    big-endian on the wire, then the payload. The bucket difference rides in
    reserved0's low word — the header's last 4 bytes."""
    hdr = (len(pld).to_bytes(4, "big") + bucket.to_bytes(4, "big") +
           pkt_id.to_bytes(8, "big") + b"\x00" * 44 + pack(">I", bkt_diff))
    assert len(hdr) == HDR_BYTES
    return hdr + pld


def ctx_pkt(nwords, seed):
    """A forwarded context packet — any word-aligned bytes (identification is
    positional, so the content is arbitrary)."""
    return rand_bytes(4 * nwords, seed)


def san_hdr(pkt_id=RSP_ID):
    """The sanitized challenge header the DUT must forward in place: only ID
    survives — PLD_LEN and RESERVED (incl. the timing word) are the answers to
    the length and timing estimates, and BUCKET is zeroed too, so the header
    carries nothing but the challenge identity. No payload."""
    return b"\x00" * 8 + pkt_id.to_bytes(8, "big") + b"\x00" * 48


def build_frame(kind, act, rng, mode="normal"):
    """One estimate frame for the actual value `act` (4 bytes).

    kind (len | time | tok) is only a label — all kinds build and score
    identically: first entry whose value matches `act` wins, the frame's last
    word is the catch-all fallback.
    modes: normal   — act listed with its own probability
           unlisted — act not listed, the catch-all scores
           normfail — probabilities sum over 1.0 -> PROB_MIN default
           bare     — a single value word, no probability -> default
    Returns (frame_bytes, expected_surprisal).
    """
    def rand_val():
        v = rand_bytes(4, rng.randrange(1 << 30))
        while v == act:
            v = rand_bytes(4, rng.randrange(1 << 30))
        return v

    def p(lo, hi):
        return prob_word(rng.randint(lo, hi), rng.randrange(1 << MANT_W))

    if mode == "bare":            # dangling word = a bare catch-all
        tail = rand_val()
        return tail, model_frame_surprisal([], act,
                                           tail=int.from_bytes(tail, "big"))
    else:
        mid = [(rand_val(), p(8, 20)) for _ in range(rng.randint(0, 2))]
        if mode == "normal":
            mid.append((act, p(3, 8)))
        if mode == "normfail":
            mid.append((rand_val(), prob_word(0, 0)))     # 1.0
            mid.append((rand_val(), prob_word(0, 0)))     # + 1.0 -> fail
        rng.shuffle(mid)
        # catch-all last: charged x 2^SPACE_W, keep it passing
        pairs = mid + \
                [(rand_val(), p(SPACE_W + 3, min(SPACE_W + 8, EXP_MAX)))]
        frame = b"".join(v + pw.to_bytes(4, "big") for v, pw in pairs)

    return frame, model_frame_surprisal(pairs, act)


def challenge_acts(tokens):
    """(kind, actual value) per estimate, in order: LEN, TIME, tok_0..tok_{L-1}."""
    L = len(tokens)
    acts = [("len", pack(">I", L)), ("time", pack(">I", BKT_DIFF))]
    # token = number zero-extended to a word (big-endian): pad in front
    acts += [("tok", b"\x00" * (4 - TOK_BYTES) + t) for t in tokens]
    return acts


def pack_beats(data: bytes):
    """bytes -> [(tdata, tkeep, tlast)] (byte 0 in the low lane)."""
    n = (len(data) + 3) // 4
    beats = []
    for w in range(n):
        chunk = data[4 * w:4 * w + 4]
        d = keep = 0
        for k, b in enumerate(chunk):
            d |= b << (8 * k)
            keep |= 1 << k
        beats.append((d, keep, int(w == n - 1)))
    return beats


# --------------------------------------------------------------------------
# port drivers / monitors
# --------------------------------------------------------------------------
async def drive_pkt(dut, data: bytes):
    """One packet on the slave (packet) port; tuser = byte length @ beat #0."""
    for w, (d, keep, last) in enumerate(pack_beats(data)):
        dut.tvalid_s.value = 1
        dut.tdata_s.value = d
        dut.tkeep_s.value = keep
        dut.tlast_s.value = last
        dut.tuser_s.value = len(data) if w == 0 else 0
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


async def drive_swap(dut):
    """One empty swap beat (tkeep = 0, tlast) — the bucket delimiter."""
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


async def drive_estimate(dut, data: bytes):
    """One estimate frame on the estimate port."""
    for d, keep, last in pack_beats(data):
        dut.tvalid_e.value = 1
        dut.tdata_e.value = d
        dut.tkeep_e.value = keep
        dut.tlast_e.value = last
        while True:
            await ReadOnly()
            ready = int(dut.tready_e.value)
            await RisingEdge(dut.clk)
            if ready:
                break
    dut.tvalid_e.value = 0
    dut.tlast_e.value = 0
    dut.tkeep_e.value = 0


async def recv_master(dut, n_packets, throttle=0.0, seed=0):
    """Collect n packets from the master port: [(bytes, tuser@beat0)]."""
    rng = random.Random(seed)
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


async def watch_u(dut, box):
    """Capture the dispatched U from the u_out port (out_valid for one
    cycle when DISPATCH exits)."""
    while True:
        await ReadOnly()
        if int(dut.out_valid.value):
            box[0] = int(dut.u_out.value)
        await RisingEdge(dut.clk)


async def reset(dut):
    cocotb.start_soon(Clock(dut.clk, 8, unit="ns").start())
    dut.rst_n.value = 0
    for sig in ("tvalid_s", "tdata_s", "tkeep_s", "tlast_s", "tuser_s",
                "tvalid_e", "tdata_e", "tkeep_e", "tlast_e", "tick"):
        getattr(dut, sig).value = 0
    dut.tready_m.value = 1
    for _ in range(5):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    for _ in range(2):
        await RisingEdge(dut.clk)


# --------------------------------------------------------------------------
# retry harness — timeouts are counted in bucket ticks
# --------------------------------------------------------------------------
TIMEOUT_TICKS = 10        # must match recomp_feed.sv
# TB-only: cycles between tick pulses. The RTL counts pulses, not cycles, so
# this only sets how long a timeout takes in simulation. Kept well clear of
# the per-position scoring time (the timeout counts down *during* scoring,
# since tready_e is held low there) so a healthy loop never times out.
TICK_PERIOD    = 100
TIMEOUT_CYCLES = (TIMEOUT_TICKS + 2) * TICK_PERIOD

# recomp_feed state_t, in declaration order
ST_ARMED, ST_CHL, ST_CTX, ST_LEN_EST, ST_TIME_EST, \
    ST_TOK_EST, ST_EMIT, ST_DISPATCH, ST_ALIGN = range(9)


async def tick_driver(dut, period=TICK_PERIOD):
    """Bucket tick: a one-cycle pulse every `period` cycles.

    Deliberately not started by reset() — every test that does not exercise
    the retry path leaves tick at 0, which is what keeps the timeout inert
    for them.
    """
    while True:
        for _ in range(period - 1):
            await RisingEdge(dut.clk)
        dut.tick.value = 1
        await RisingEdge(dut.clk)
        dut.tick.value = 0


async def collect_master(dut, out):
    """Collect master-port packets into `out` as (bytes, tuser@beat0),
    indefinitely — unlike recv_master, which stops after a fixed count."""
    dut.tready_m.value = 1
    cur, tuser0 = bytearray(), None
    while True:
        await ReadOnly()
        if int(dut.tvalid_m.value) and int(dut.tready_m.value):
            d, keep = int(dut.tdata_m.value), int(dut.tkeep_m.value)
            if tuser0 is None:
                tuser0 = int(dut.tuser_m.value)
            for k in range(4):
                if (keep >> k) & 1:
                    cur.append((d >> (8 * k)) & 0xFF)
            if int(dut.tlast_m.value):
                out.append((bytes(cur), tuser0))
                cur, tuser0 = bytearray(), None
        await RisingEdge(dut.clk)


async def count_pulses(dut, sig, box):
    """Count cycles on which `sig` is asserted into box[0], indefinitely.
    The bucket-retry controls are single-cycle pulses, so cycles == pulses."""
    box[0] = 0
    while True:
        await ReadOnly()
        if int(sig.value):
            box[0] += 1
        await RisingEdge(dut.clk)


async def await_frames(dut, out, n, timeout=200_000, what="frames"):
    for _ in range(timeout):
        if len(out) >= n:
            return
        await RisingEdge(dut.clk)
    raise AssertionError(f"timeout waiting for {n} {what} (got {len(out)})")


def reveal(i, tok):
    """The one-beat reveal frame for position i, as collect_master sees it."""
    return (pack(">H", i) + tok, 4)


async def stage_challenge(dut, ctx, tokens):
    """Drive one bucket: challenged response first, context, closing swap."""
    await drive_pkt(dut, rsp_pkt(b"".join(tokens)))
    for p in ctx:
        await drive_pkt(dut, p)
    await drive_swap(dut)


def est_frames(tokens, seed):
    """Every estimate frame for the loop, plus the exact model U."""
    rng = random.Random(seed)
    frames, u = [], 0
    for kind, act in challenge_acts(tokens):
        fb, s = build_frame(kind, act, rng)
        frames.append(fb)
        u += s
    return frames, u


# --------------------------------------------------------------------------
# challenge harness
# --------------------------------------------------------------------------
async def run_challenge(dut, ctx, tokens, throttle=0.0, seed=0, modes=None,
                        lead_swaps=0, extra_pkts=()):
    """Drive [lead swaps +] response + ctx + closing swap, answer with built
    estimate frames, check the forwarded stream (sanitized header + ctx +
    reveals), and the final U against the model.

    lead_swaps: bare delimiters ahead of the response — empty buckets when
    armed, the re-sync boundary after a previous challenge. extra_pkts are
    driven right after the closing swap, while the challenge runs: they must
    be dropped whole (a forwarded one would corrupt the output checks).
    """
    pld = b"".join(tokens)
    resp = rsp_pkt(pld)
    L = len(tokens)
    n_rev = max(L - 1, 0)                   # the last token is never revealed
    n_out = 1 + len(ctx) + n_rev            # sanitized header + ctx + reveals

    acts = challenge_acts(tokens)
    modes = modes or ["normal"] * len(acts)
    assert len(modes) == len(acts)
    rng = random.Random(1000 + seed)
    frames, u_model = [], 0
    for (kind, act), mode in zip(acts, modes):
        fb, s = build_frame(kind, act, rng, mode)
        frames.append(fb)
        u_model += s

    u_box = [0]
    uw = cocotb.start_soon(watch_u(dut, u_box))
    mon = cocotb.start_soon(recv_master(dut, n_out, throttle=throttle, seed=7 + seed))

    async def drive_slave():
        for _ in range(lead_swaps):
            await drive_swap(dut)
        await drive_pkt(dut, resp)          # the bucket's first packet
        for p in ctx:
            await drive_pkt(dut, p)
        await drive_swap(dut)               # closing swap: arms the estimates
        for p in extra_pkts:                # mid-challenge: dropped whole
            await drive_pkt(dut, p)

    async def drive_ests():
        for fb in frames:
            await drive_estimate(dut, fb)

    slv = cocotb.start_soon(drive_slave())
    est = cocotb.start_soon(drive_ests())

    await Combine(slv, est, mon)
    out = mon.result()

    assert out[0] == (san_hdr(), HDR_BYTES), \
        f"bad sanitized header: {out[0][0].hex()} != {san_hdr().hex()}"
    for k, p in enumerate(ctx):
        assert out[1 + k] == (p, len(p)), \
            f"ctx {k}: {out[1 + k]} != {(p.hex(), len(p))}"
    for i in range(n_rev):
        rev = out[1 + len(ctx) + i]
        # reveal frame = (index big-endian 2B, token wire order), tuser = 4
        assert rev == (pack(">H", i) + tokens[i], 4), \
            f"token {i}: {rev[0].hex()} (u={rev[1]}) != {pack('>H', i).hex()}{tokens[i].hex()}"

    for _ in range(2 * FRAC_W + 40):          # final frame's scoring drains
        await RisingEdge(dut.clk)
    uw.cancel()
    assert u_box[0] == u_model, f"U: got {u_box[0]:#x}, model {u_model:#x}"


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------
@cocotb.test()
async def test_basic(dut):
    """Response (bucket's first packet) forwarded as its sanitized header,
    payload captured, context forwarded, tokens revealed, U matches the
    exact model."""
    await reset(dut)
    ctx = [ctx_pkt(3 + k, 10 + k) for k in range(2)]
    tokens = [rand_bytes(TOK_BYTES, 200 + k) for k in range(5)]
    await run_challenge(dut, ctx, tokens)


@cocotb.test()
async def test_no_context(dut):
    """Response immediately closed by the swap beat (no context packets)."""
    await reset(dut)
    tokens = [rand_bytes(TOK_BYTES, 300 + k) for k in range(4)]
    await run_challenge(dut, [], tokens)


@cocotb.test()
async def test_empty_buckets(dut):
    """Bare swap beats ahead of the challenge (empty or withheld buckets):
    consumed in place, never forwarded, and the first real packet after them
    is still taken as the challenged response."""
    await reset(dut)
    tokens = [rand_bytes(TOK_BYTES, 350 + k) for k in range(3)]
    await run_challenge(dut, [ctx_pkt(4, 35)], tokens, lead_swaps=3)


@cocotb.test()
async def test_single_token(dut):
    """L = 1: the token's estimate is scored, but the token itself is never
    revealed (no reveals for the final token, no EOS)."""
    await reset(dut)
    await run_challenge(dut, [ctx_pkt(4, 1)], [rand_bytes(TOK_BYTES, 99)])


@cocotb.test()
async def test_backpressure(dut):
    """Throttled master port: forwarding and reveals still correct/in order."""
    await reset(dut)
    ctx = [ctx_pkt(5, 40 + k) for k in range(3)]
    tokens = [rand_bytes(TOK_BYTES, 400 + k) for k in range(6)]
    await run_challenge(dut, ctx, tokens, throttle=0.4)


@cocotb.test()
async def test_two_challenges(dut):
    """Back-to-back challenges: state (incl. U) fully re-arms. The second
    challenge needs a swap beat first — the block re-syncs on a bucket
    boundary after a challenge, never mid-bucket."""
    await reset(dut)
    for c in range(2):
        ctx = [ctx_pkt(3, 500 + 10 * c + k) for k in range(1 + c)]
        tokens = [rand_bytes(TOK_BYTES, 600 + 10 * c + k) for k in range(2 + c)]
        await run_challenge(dut, ctx, tokens, seed=c, lead_swaps=min(c, 1))


@cocotb.test()
async def test_drop_mid_challenge(dut):
    """Packets released while a challenge runs are dropped whole — never
    forwarded (a forwarded one would corrupt the output checks) — and the
    block still re-arms cleanly on the next swap beat."""
    await reset(dut)
    tokens = [rand_bytes(TOK_BYTES, 460 + k) for k in range(3)]
    intruders = [ctx_pkt(6, 461), ctx_pkt(2, 462)]
    await run_challenge(dut, [ctx_pkt(4, 46)], tokens, extra_pkts=intruders)
    # a fresh challenge after the drops proves the re-sync
    tokens = [rand_bytes(TOK_BYTES, 470 + k) for k in range(2)]
    await run_challenge(dut, [], tokens, seed=1, lead_swaps=1)


@cocotb.test()
async def test_full_packet(dut):
    """Full packet (tok_total == TOK_MAX): TOK_MAX token estimates are scored,
    the last token is NOT revealed — the loop ends after TOK_MAX-1 reveals."""
    await reset(dut)
    tokens = [rand_bytes(TOK_BYTES, 700 + k) for k in range(TOK_MAX)]
    await run_challenge(dut, [ctx_pkt(3, 1)], tokens)


@cocotb.test()
async def test_empty_response(dut):
    """Degenerate PLD_LEN = 0 response: no token estimates and no reveals —
    only LEN and TIME are scored."""
    await reset(dut)
    await run_challenge(dut, [], [])


@cocotb.test()
async def test_score_unlisted(dut):
    """Actual values not listed: the catch-all probability scores."""
    await reset(dut)
    tokens = [rand_bytes(TOK_BYTES, 800 + k) for k in range(3)]
    modes = ["normal", "unlisted", "unlisted", "normal", "unlisted"]
    await run_challenge(dut, [], tokens, seed=3, modes=modes)


@cocotb.test()
async def test_score_normfail(dut):
    """Normalization failures charge the max-entropy default."""
    await reset(dut)
    tokens = [rand_bytes(TOK_BYTES, 900 + k) for k in range(2)]
    modes = ["normfail", "normal", "normal", "normfail"]
    await run_challenge(dut, [ctx_pkt(3, 2)], tokens, seed=4, modes=modes)


@cocotb.test()
async def test_score_bare(dut):
    """A single-word frame: the dangling word is a bare catch-all — it norms
    x 2^W (PROB_MIN when that fails) and scores as the probability."""
    await reset(dut)
    tokens = [rand_bytes(TOK_BYTES, 950 + k) for k in range(2)]
    modes = ["bare", "normal", "bare", "normal"]
    await run_challenge(dut, [], tokens, seed=5, modes=modes)


@cocotb.test()
async def test_score_mixed_fuzz(dut):
    """Random mode mix across several challenges, exact U each time."""
    await reset(dut)
    rng = random.Random(77)
    for c in range(4):
        tokens = [rand_bytes(TOK_BYTES, 2000 + 10 * c + k)
                  for k in range(rng.randint(1, 5))]
        acts_n = len(challenge_acts(tokens))
        modes = [rng.choice(["normal", "normal", "unlisted", "normfail", "bare"])
                 for _ in range(acts_n)]
        # every challenge but the first needs a bucket boundary to re-arm on
        await run_challenge(dut, [], tokens, seed=100 + c, modes=modes,
                            lead_swaps=min(c, 1))


# --------------------------------------------------------------------------
# retry (packet loss on the estimate path)
# --------------------------------------------------------------------------
@cocotb.test()
async def test_reveal_retry_resends_last_reveal(dut):
    """A lost mid-loop estimate is recovered by re-sending the last reveal.

    Interlock and enclosure run in lockstep — each reveal prompts the next
    estimate — so a lost estimate leaves both waiting. The interlock times out
    and re-sends the previous reveal (index and value) to prompt again; the
    enclosure answers, the position is scored exactly once, and U comes out
    identical to the lossless run.

    The bucket is not replayed here: the enclosure still holds the context, so
    only the prompt is repeated. bkt_replay must stay low throughout, and the
    lone out_valid pulse — which doubles as batch_buffer's release ack —
    lands with U.
    """
    await reset(dut)
    cocotb.start_soon(tick_driver(dut))
    out, u_box = [], [0]
    n_replay, n_ack = [0], [0]
    cocotb.start_soon(collect_master(dut, out))
    cocotb.start_soon(count_pulses(dut, dut.bkt_replay, n_replay))
    cocotb.start_soon(count_pulses(dut, dut.out_valid, n_ack))
    uw = cocotb.start_soon(watch_u(dut, u_box))

    tokens = [rand_bytes(TOK_BYTES, 600 + k) for k in range(5)]
    ctx = [ctx_pkt(3, 61)]
    frames, u_model = est_frames(tokens, seed=4242)

    DROP = 4                      # frames are LEN, TIME, tok0, tok1, tok2 ...

    cocotb.start_soon(stage_challenge(dut, ctx, tokens))

    for i, fb in enumerate(frames):
        if i == DROP:
            # the loss: stay silent past the timeout so the block re-prompts,
            # then answer the retry with the same frame
            for _ in range(TIMEOUT_CYCLES):
                await RisingEdge(dut.clk)
        await drive_estimate(dut, fb)

    # sanitized header + context + reveals 0, 1, 1(retry), 2, 3
    await await_frames(dut, out, 2 + 5, what="forwarded/reveal")
    assert out[0] == (san_hdr(), HDR_BYTES), f"challenge header: {out[0]}"
    assert out[1] == (ctx[0], len(ctx[0])), f"context: {out[1]}"

    want = [reveal(0, tokens[0]), reveal(1, tokens[1]),
            reveal(1, tokens[1]),                       # the retry
            reveal(2, tokens[2]), reveal(3, tokens[3])]
    assert out[2:7] == want, f"reveal sequence:\n got {out[2:7]}\nwant {want}"

    for _ in range(2 * FRAC_W + 40):
        await RisingEdge(dut.clk)
    uw.cancel()
    assert u_box[0] == u_model, f"U: got {u_box[0]:#x}, model {u_model:#x}"
    assert n_replay[0] == 0, \
        f"reveal-level retry must not replay the bucket, saw {n_replay[0]}"
    assert n_ack[0] == 1, \
        f"expected one out_valid (bucket release ack), saw {n_ack[0]}"


@cocotb.test()
async def test_timeout_before_any_reveal_must_not_leak_token0(dut):
    """A timeout at idx == 0 must not reveal token 0.

    Nothing has been revealed yet there: the block is waiting on the
    enclosure's estimate of token 0, the first thing it must predict unaided.
    So this is challenge-level retry territory — the block pulses bkt_replay
    and drops back to ARMED to take the replayed bucket, rather than handing
    the enclosure a token it never estimated.
    """
    await reset(dut)
    cocotb.start_soon(tick_driver(dut))
    out = []
    cocotb.start_soon(collect_master(dut, out))

    tokens = [rand_bytes(TOK_BYTES, 700 + k) for k in range(4)]
    ctx = [ctx_pkt(3, 71)]
    frames, _ = est_frames(tokens, seed=99)

    cocotb.start_soon(stage_challenge(dut, ctx, tokens))
    await drive_estimate(dut, frames[0])            # LEN
    await drive_estimate(dut, frames[1])            # TIME
    # now in TOK_EST at idx == 0, awaiting token 0's estimate — which is lost
    await await_frames(dut, out, 2, what="header+context")
    n_before = len(out)

    n_replay = [0]
    replays = cocotb.start_soon(count_pulses(dut, dut.bkt_replay, n_replay))
    for _ in range(TIMEOUT_CYCLES):
        await RisingEdge(dut.clk)
    replays.cancel()

    assert len(out) == n_before, (
        f"revealed {out[n_before][0].hex()} before its estimate ever arrived")
    # the challenge is retried, not silently dropped: exactly one replay
    # request, and ARMED (not ALIGN) so the replayed bucket's first packet
    # is taken as the challenged response — its swap beat trails the data,
    # so ALIGN would consume the whole replay before re-arming
    assert n_replay[0] == 1, \
        f"expected one bkt_replay pulse, saw {n_replay[0]}"
    assert int(dut.state.value) == ST_ARMED, \
        f"state {int(dut.state.value)} after a challenge-level retry, want ARMED"


@cocotb.test()
async def test_lost_opening_estimate_recovers(dut):
    """A lost LEN or TIME estimate abandons the challenge instead of hanging.

    No reveal has happened when an opening estimate is lost, so this is the
    other challenge-level retry case: LEN_EST and TIME_EST each time out to
    ARMED with a bkt_replay request. Without that the block sat in LEN_EST
    forever — U never dispatched, never back to ARMED — so one lost frame
    killed the recomputation path until reset.
    """
    await reset(dut)
    cocotb.start_soon(tick_driver(dut))
    out = []
    cocotb.start_soon(collect_master(dut, out))

    tokens = [rand_bytes(TOK_BYTES, 800 + k) for k in range(3)]
    cocotb.start_soon(stage_challenge(dut, [ctx_pkt(3, 81)], tokens))
    await await_frames(dut, out, 2, what="header+context")

    # never send the LEN estimate
    n_replay = [0]
    replays = cocotb.start_soon(count_pulses(dut, dut.bkt_replay, n_replay))
    for _ in range(3 * TIMEOUT_CYCLES):
        await RisingEdge(dut.clk)
    replays.cancel()

    assert int(dut.state.value) != ST_LEN_EST, (
        "still in LEN_EST long after the timeout window — a lost opening "
        "estimate is unrecoverable")
    assert int(dut.state.value) == ST_ARMED, \
        f"state {int(dut.state.value)} after a lost LEN estimate, want ARMED"
    # one replay request, and only one: the block waits for the bucket rather
    # than re-requesting every timeout window while parked in ARMED
    assert n_replay[0] == 1, \
        f"expected one bkt_replay pulse, saw {n_replay[0]}"
