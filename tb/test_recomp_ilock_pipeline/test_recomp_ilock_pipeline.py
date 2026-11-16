"""cocotb integration TB for recomp_ilock_core.sv — silicon bug reproduction.

Three investigations on the MAC-level pipeline (port 0 = prover frontend,
port 1 = recomputation enclosure), all driven wire-shaped (CoreTSE MAC-FIFO
words, real Ethernet LENGTH frames):

  1. estimate_ingress        — happy-path challenge with the exact estimate
                               frames used on the silicon bring-up: LEN, TIME
                               and one per token, no terminal EOS estimate
                               (everything listed at p = 0.5, so
                               U-hat = n_frames << 25),
                               plus two diagnosis variants that DO reproduce
                               the silicon "silent estimate path" (no reveals,
                               no U-hat): Type-framed estimates, and a
                               bucket-stale challenged response.
  2. buffer_stall_runaway    — reproduce the endless LENGTH=0 Ethernet frame
                               flood on port 1 TX: a challenge stalls across
                               many ticks while traffic keeps banking; when it
                               completes, forwarding resumes against a
                               wire-rate-limited TX MAC (modelled by a
                               throttled MTX accept — the block TBs' always-
                               ready sink is exactly what hid this), the tick
                               preempts the batch_buffer drain mid-record, and
                               eth_reframe latches a mid-packet tuser of 0 as
                               the frame LENGTH: data_end == ETH_HDR_BYTES
                               means `feeding` is never true, so it emits
                               LENGTH=0 frames forever without consuming its
                               pending input beat.
  3. orphaned CTRL chains    — CTRL(+response) pairs arriving while a
                               challenge is in flight are dropped whole (the
                               enclosure never sees the second START, and the
                               estimates it eventually sends are scored
                               against challenge #1); CTRL immediately
                               followed by CTRL captures the second CTRL as an
                               empty response (tok_total = 0, so the token loop
                               is skipped entirely) and dispatches U-hat under
                               id 0 — colliding with the reserved
                               CANON_CERT_ID / "no challenge yet" encoding.

Frame builders and the bit-exact U model come from test_recomp_feed
(PYTHONPATH); the MAC-FIFO drivers are adapted from test_recomp_ilock_core.

DUT parameters (Makefile -P): TIMER_END = 4999, BKTS_PER_CERT = 4. NOTE
buffer_chl hardcodes GRACE_PERIOD = 2000 and the drain window is
(GRACE_PERIOD-1 .. tick), so TIMER_END must stay well above 2000.
"""
from struct import pack

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly

import commit_model as cm      # the digest the ARM marker has to promise
import test_recomp_feed as rf   # frame surprisal model + rand_bytes

TIMER_END     = 4999            # must match the Makefile -P overrides
BKTS_PER_CERT = 1
GRACE_PERIOD  = 2000            # hardcoded in recomp_ilock_core's buffer_chl

HDR_BYTES = 64                  # canonical header (both directions, 64 B)
SYNC_LEN  = 64                  # canonical sync packet (id == CANON_SYNC_ID)
CERT_LEN  = 64 + 128 + 32       # zero header + cert_msg_t + tau (M_BYTES = 128)

Z4 = b"\x00" * 4

FEED_ST = ["ARMED", "CHL", "CTX", "LEN_EST", "TIME_EST", "TOK_EST",
           "EMIT", "DISPATCH", "ALIGN"]


# --------------------------------------------------------------------------
# Ethernet / canonical builders
# --------------------------------------------------------------------------
def eth_wrap(payload, len_type=None):
    """dst + src + LENGTH + payload (+ pad to 46) + 4-byte FCS placeholder.
    len_type overrides the LENGTH field (e.g. 0x0800 to build a Type frame)."""
    body = payload + b"\x00" * max(0, 46 - len(payload))
    lt = len(payload) if len_type is None else len_type
    return (b"\x02\x00\x00\x00\x00\x02" + b"\x02\x00\x00\x00\x00\x01" +
            pack(">H", lt) + body + b"\x00" * 4)


def canon_req(id_cont, pld, bucket=0, inf=1, bkt_diff=0, ref=0, rsvd=0):
    """Canonical packet for the recomp ingress (canon_proc RECOMP = 1: only
    the integrity checks apply — full header, bucket match, PLD_LEN, byte
    count — and inference packets only, hence inf defaulting to 1).
    bkt_diff rides in the last header word (the RSP-parse reserved0 low word
    recomp_feed reads)."""
    pkt_id = (id_cont << 1) | inf     # canon_id_t: inf is ID[0]
    return (pack(">I", len(pld)) + pack(">I", bucket) + pack(">Q", pkt_id) +
            pack(">Q", ref) + pack(">Q", rsvd) +
            b"\x00" * 28 + pack(">I", bkt_diff) + pld)


ARM_ID = 1                      # expected-slice-digest marker


def arm_marker(digest, bucket=0):
    """ARM marker: header-only ID = 1 packet carrying the expected slice
    digest in KEY_COMMIT. canon_proc latches the field and drops the packet
    (reserved ID), so it is not part of the commitment it describes and holds
    no position in the slice."""
    return (pack(">I", 0) + pack(">I", bucket) + pack(">Q", ARM_ID) +
            pack(">Q", 0) + pack(">Q", 0) + digest.rjust(32, b"\x00"))


def san_hdr(pkt_id):
    """The sanitized challenge header recomp_feed forwards in place of the
    challenged response: only ID survives, payload stripped."""
    return b"\x00" * 8 + pack(">Q", pkt_id) + b"\x00" * 48


def wire_id(id_cont, inf=1):
    return (id_cont << 1) | inf


# --------------------------------------------------------------------------
# Estimate frames — the exact shapes driven on the silicon bring-up
# --------------------------------------------------------------------------
def pw(exp):
    """Probability word: mant = 0 -> value 2^-exp (recomp_pkg prob_t)."""
    return exp << 26


def frames_from_specs(specs):
    """specs: [(kind, [(val4, prob_word)], act4)] -> (frames, exact U model).
    `kind` is a label only — every kind scores identically."""
    frames, u = [], 0
    for _kind, pairs, act in specs:
        frames.append(b"".join(v + p.to_bytes(4, "big") for v, p in pairs))
        u += rf.model_frame_surprisal(pairs, act)
    return frames, u


def silicon_est_frames(tokens, bkt_diff):
    """LEN [(L,2^-1),(0,2^-33)] | TIME [(bkt_diff,2^-1),(0,2^-33)] |
    TOK [(0,2^-2),(tok,2^-1),(0,2^-34)] per token.
    One token estimate per position, no terminal EOS estimate (length scoring
    covers premature termination). Every actual is listed at p = 0.5 ->
    U = (len(tokens)+2) << 25.

    The actual values come from rf.challenge_acts, so the on-wire value
    encoding lives in exactly one place — tokens are zero-extended to a word
    big-endian, i.e. padded IN FRONT (padding at the end scores the catch-all
    instead of the token)."""
    assert bkt_diff == rf.BKT_DIFF, "challenge_acts pins the timing actual"
    acts = [act for _kind, act in rf.challenge_acts(tokens)]
    specs = [("len",  [(acts[0], pw(1)), (Z4, pw(33))], acts[0]),
             ("time", [(acts[1], pw(1)), (Z4, pw(33))], acts[1])]
    for act in acts[2:]:
        specs.append(("tok", [(Z4, pw(2)), (act, pw(1)), (Z4, pw(34))], act))
    return frames_from_specs(specs)


def empty_capture_frames():
    """Estimates for a captured CTRL (pld_len = 0, bkt_diff = 0): LEN act 0
    and TIME act 0 only — with tok_total = 0 the feed skips the token loop
    entirely and dispatches straight after TIME."""
    L0 = pack(">I", 0)
    specs = [("len",  [(L0, pw(1)), (Z4, pw(33))], L0),
             ("time", [(Z4, pw(1)), (Z4, pw(33))], Z4)]
    return frames_from_specs(specs)


# --------------------------------------------------------------------------
# CoreTSE MAC-client drivers / monitors
# --------------------------------------------------------------------------
def words_of(frame):
    """frame bytes -> [(dat, bytevalid, last)] — bytevalid counts INVALID
    bytes in the final word (CoreTSE convention)."""
    n = (len(frame) + 3) // 4
    out = []
    for w in range(n):
        chunk = frame[4 * w:4 * w + 4]
        dat = int.from_bytes(chunk.ljust(4, b"\x00"), "little")
        out.append((dat, (4 - len(chunk)) % 4, w == n - 1))
    return out


async def mac_drive(dut, pfx, frame, max_wait=None):
    """Drive one frame into an MRX bundle (rdy/acpt handshake). Returns True
    when fully accepted; with max_wait set, abandons the frame after a word
    stalls that many cycles (the silicon-equivalent of the MAC RX FIFO
    overflowing) and returns False."""
    rdy  = getattr(dut, f"{pfx}_rdy")
    acpt = getattr(dut, f"{pfx}_acpt")
    ok = True
    for i, (dat, bv, last) in enumerate(words_of(frame)):
        rdy.value = 1
        getattr(dut, f"{pfx}_sof").value = int(i == 0)
        getattr(dut, f"{pfx}_eof").value = int(last)
        getattr(dut, f"{pfx}_dat").value = dat
        getattr(dut, f"{pfx}_bytevalid").value = bv
        waited = 0
        while True:
            await ReadOnly()
            taken = int(acpt.value)
            await RisingEdge(dut.clk)
            if taken:
                break
            waited += 1
            if max_wait is not None and waited > max_wait:
                ok = False
                break
        if not ok:
            break
    rdy.value = 0
    getattr(dut, f"{pfx}_sof").value = 0
    getattr(dut, f"{pfx}_eof").value = 0
    return ok


async def mac_monitor(dut, pfx, frames, own_acpt=True):
    """Collect frames from an MTX bundle into `frames` as (LENGTH, payload)
    tuples (Ethernet header stripped, LENGTH-sliced — drops pad and FCS).
    own_acpt=False leaves the accept signal to a throttle coroutine."""
    acpt = getattr(dut, f"{pfx}_acpt")
    if own_acpt:
        acpt.value = 1
    cur = bytearray()
    while True:
        await ReadOnly()
        if int(getattr(dut, f"{pfx}_rdy").value) and int(acpt.value):
            if int(getattr(dut, f"{pfx}_sof").value):
                cur = bytearray()
            dat = int(getattr(dut, f"{pfx}_dat").value)
            cur += dat.to_bytes(4, "little")
            if int(getattr(dut, f"{pfx}_eof").value):
                bv = int(getattr(dut, f"{pfx}_bytevalid").value)
                if bv:
                    cur = cur[:-bv]
                length = int.from_bytes(cur[12:14], "big")
                frames.append((length, bytes(cur[14:14 + length])))
        await RisingEdge(dut.clk)


async def throttle_acpt(dut, pfx, duty=6):
    """Accept ~1 word in `duty` cycles — models the CoreTSE TX FIFO draining
    at gigabit wire rate (~0.16 words/cycle at the 80 MHz fabric clock)
    instead of the always-ready sink the block-level TBs use."""
    sig = getattr(dut, f"{pfx}_acpt")
    n = 0
    while True:
        sig.value = 1 if (n % duty) == 0 else 0
        n += 1
        await RisingEdge(dut.clk)


async def wait_for(dut, cond, timeout, what):
    """Spin until cond() returns non-None, or raise after `timeout` cycles."""
    for _ in range(timeout):
        v = cond()
        if v is not None:
            return v
        await RisingEdge(dut.clk)
    raise AssertionError(f"timeout ({timeout} cycles) waiting for {what}")


async def reset(dut):
    cocotb.start_soon(Clock(dut.clk, 8, unit="ns").start())
    dut.rst_n.value = 0
    for pfx in ("tse0_mrx", "tse1_mrx"):
        getattr(dut, f"{pfx}_rdy").value = 0
        getattr(dut, f"{pfx}_sof").value = 0
        getattr(dut, f"{pfx}_eof").value = 0
        getattr(dut, f"{pfx}_dat").value = 0
        getattr(dut, f"{pfx}_bytevalid").value = 0
    dut.tse0_mtx_acpt.value = 1
    dut.tse1_mtx_acpt.value = 1
    for _ in range(5):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    for _ in range(2):
        await RisingEdge(dut.clk)


# --------------------------------------------------------------------------
# bucket-timer sync (canon_proc drops any packet whose bucket != curr_bkt)
# --------------------------------------------------------------------------
async def bucket_window(dut, need=600):
    """Wait until at least `need` cycles remain before the next tick; return
    the live bucket index (read hierarchically from canon_proc_chl)."""
    while True:
        if int(dut.timer.value) < TIMER_END - need:
            return int(dut.canon_proc_chl.curr_bkt.value)
        await RisingEdge(dut.clk)


async def next_bucket(dut):
    b = int(dut.canon_proc_chl.curr_bkt.value)
    while int(dut.canon_proc_chl.curr_bkt.value) == b:
        await RisingEdge(dut.clk)


# --------------------------------------------------------------------------
# recomp_feed probe — state trace + estimate-port accounting + U dispatches
# --------------------------------------------------------------------------
class FeedProbe:
    def __init__(self, dut):
        self.dut = dut
        self.cyc = 0
        self.states = []            # (cycle, state name)
        self.est_beats = 0          # estimate-port beats consumed
        self.est_frames = 0         # estimate frames completed (tlast beats)
        self.est_stall = 0          # cycles tvalid_e && !tready_e
        self.u_events = []          # (id_out, u_out) per dispatch
        cocotb.start_soon(self._run())

    async def _run(self):
        feed = self.dut.u_feed
        prev_s, prev_v = None, 0
        while True:
            await ReadOnly()
            s = int(feed.state.value)
            if s != prev_s:
                self.states.append(
                    (self.cyc, FEED_ST[s] if s < len(FEED_ST) else str(s)))
                prev_s = s
            tv, tr = int(feed.tvalid_e.value), int(feed.tready_e.value)
            if tv and tr:
                self.est_beats += 1
                if int(feed.tlast_e.value):
                    self.est_frames += 1
            elif tv and not tr:
                self.est_stall += 1
            ov = int(feed.out_valid.value)
            if ov and not prev_v:
                self.u_events.append(
                    (int(feed.id_out.value), int(feed.u_out.value)))
            prev_v = ov
            await RisingEdge(self.dut.clk)
            self.cyc += 1

    @property
    def state(self):
        return self.states[-1][1] if self.states else "?"

    def summary(self):
        return (f"feed state trace (last 12): {self.states[-12:]} | "
                f"est beats={self.est_beats} frames={self.est_frames} "
                f"stalled-cycles={self.est_stall} | "
                f"U dispatches={[(hex(i), hex(u)) for i, u in self.u_events]}")


def cert_outward(cert):
    """OVERALL_RSP in RECOMP mode: {overall_req_match, 127 x 0, id, U} —
    the release decision for this certificate's bucket in the MSB, then the
    scored response's id and U. Returns (match, id, U).
    (m: 4x4 B scalars + 16 B nonce + 32 B overall_req = 64 B before it.)"""
    o = cert[64 + 32 + 32: 64 + 32 + 64]
    return (o[0] >> 7,
            int.from_bytes(o[16:24], "big"),
            int.from_bytes(o[24:32], "big"))


def certs_of(frames):
    return [p for l, p in frames if l == CERT_LEN]


async def drive_challenge(dut, bucket, id_cont, tokens, bkt_diff, ctx=()):
    """Stage one challenge slice into port 0, all stamped with `bucket`.

    Arming is positional, so the challenged response goes FIRST and the
    context behind it. The ARM marker carries the digest of the slice as
    committed — the response plus the context, markers excluded — without
    which the release gate never opens and nothing reaches the enclosure.
    """
    resp = canon_req(id_cont, b"".join(tokens), bucket=bucket,
                     bkt_diff=bkt_diff)
    slice_pkts = [resp] + list(ctx)
    digest = cm.period({bucket: slice_pkts}, BKTS_PER_CERT)
    await mac_drive(dut, "tse0_mrx", eth_wrap(arm_marker(digest, bucket)))
    for p in slice_pkts:
        await mac_drive(dut, "tse0_mrx", eth_wrap(p))
    return resp


def is_san_hdr(frame, pkt_id):
    """The sanitized challenge header as mac_monitor reports it."""
    length, pld = frame
    return length == HDR_BYTES and pld == san_hdr(pkt_id)


# ==========================================================================
# 1. estimate_ingress — happy path, silicon-shaped
# ==========================================================================
@cocotb.test()
async def test_estimate_ingress(dut):
    """Armed slice in port 0, the silicon-shaped estimate frames in port 1.

    The challenged response leaves only as its sanitized header, the two
    non-final tokens are revealed with exact values, and the certificate
    carries the challenge id with a bit-exact U-hat (everything listed at
    p = 0.5, so U-hat = 5 << 25)."""
    await reset(dut)
    enc, fe = [], []
    cocotb.start_soon(mac_monitor(dut, "tse1_mtx", enc))
    cocotb.start_soon(mac_monitor(dut, "tse0_mtx", fe))
    probe = FeedProbe(dut)

    tokens = [b"\xaa\x01", b"\xbb\x02", b"\xcc\x03"]
    rid = wire_id(0x0BADF00D)
    b = await bucket_window(dut, 800)
    resp = await drive_challenge(dut, b, 0x0BADF00D, tokens, rf.BKT_DIFF)

    try:
        # the slice reaches the enclosure only once the gate opens and the
        # bank drains (next tick + grace)
        await wait_for(dut, lambda: enc[0] if enc else None,
                       3 * (TIMER_END + 1), "sanitized header on port 1")
        assert is_san_hdr(enc[0], rid), \
            f"challenge header not sanitized: {enc[0]}"
        for _ in range(500):
            await RisingEdge(dut.clk)
        assert len(enc) == 1, f"unexpected port-1 frames before estimates: {enc[1:]}"

        # estimate frames, wire-shaped (short LENGTH frames, padded)
        frames, u_model = silicon_est_frames(tokens, rf.BKT_DIFF)
        assert len(frames) == 2 + len(tokens)
        assert u_model == 5 << 25          # sanity: everything listed at 0.5
        for fb in frames:
            await mac_drive(dut, "tse1_mrx", eth_wrap(fb))

        # one one-beat TOKEN reveal per non-final token estimate, exact values
        n_rev = len(tokens) - 1
        await wait_for(dut, lambda: True if len(enc) >= 1 + n_rev else None,
                       2 * (TIMER_END + 1), f"{n_rev} token reveals on port 1")
        assert enc[1:1 + n_rev] == [(4, pack(">H", i) + t)
                                    for i, t in enumerate(tokens[:-1])], \
            f"reveals mismatch: {enc[1:1 + n_rev]}"
        assert all(f != (len(resp), resp) for f in enc), "response leaked"

        # U-hat dispatch: challenge id + bit-exact accumulator
        await wait_for(dut, lambda: probe.u_events[0] if probe.u_events else None,
                       TIMER_END, "U-hat dispatch")
        assert probe.u_events[0] == (rid, u_model), \
            f"U dispatch: {probe.u_events[0]} != {(hex(rid), hex(u_model))}"

        # ... and a certificate carries it. The release decision rides the
        # cert for the bucket it released, which is an earlier one than the
        # cert carrying U — U only exists once the estimate loop ends.
        def hot_cert():
            for c in certs_of(fe):
                _, cid, u = cert_outward(c)
                if (cid, u) != (0, 0):
                    return (cid, u)
            return None
        got = await wait_for(dut, hot_cert, 8 * (TIMER_END + 1),
                             "certificate with non-zero OVERALL_RSP")
        assert got == (rid, u_model), \
            f"cert OVERALL_RSP: {(hex(got[0]), hex(got[1]))} != " \
            f"{(hex(rid), hex(u_model))}"
        assert any(cert_outward(c)[0] for c in certs_of(fe)), \
            "no certificate reports the release that let the slice through"
    except AssertionError:
        dut._log.error("DIAG: " + probe.summary())
        raise


# --------------------------------------------------------------------------
# 1a. diagnosis variant: estimates as Type frames (LEN/TYPE > 1500)
# --------------------------------------------------------------------------
@cocotb.test()
async def test_estimate_ingress_type_frames(dut):
    """Same challenge, but the estimate frames carry an EtherType (0x0800)
    instead of a LENGTH — eth_deframe suppresses them wholesale (its
    beats_total is forced to 0 for LEN/TYPE > ETH_LEN_MAX), so recomp_feed
    never sees a single estimate beat: the slice forwards, then total silence
    (no reveals, no U-hat) with the feed parked in LEN_EST — the silicon
    fingerprint."""
    await reset(dut)
    enc = []
    cocotb.start_soon(mac_monitor(dut, "tse1_mtx", enc))
    probe = FeedProbe(dut)

    tokens = [b"\xaa\x01", b"\xbb\x02", b"\xcc\x03"]
    b = await bucket_window(dut, 800)
    await drive_challenge(dut, b, 0x0BADF00D, tokens, rf.BKT_DIFF)
    await wait_for(dut, lambda: enc[0] if enc else None,
                   3 * (TIMER_END + 1), "sanitized header on port 1")
    await wait_for(dut, lambda: True if probe.state == "LEN_EST" else None,
                   TIMER_END, "feed armed (LEN_EST)")

    frames, _ = silicon_est_frames(tokens, rf.BKT_DIFF)
    for fb in frames:
        # Type frame: identical payload, LEN/TYPE = 0x0800
        ok = await mac_drive(dut, "tse1_mrx", eth_wrap(fb, len_type=0x0800),
                             max_wait=2000)
        assert ok, "MAC never stalls: deframe accepts and drops Type frames"

    for _ in range(3000):
        await RisingEdge(dut.clk)
    dut._log.info("DIAG(type-frames): " + probe.summary())
    assert probe.est_beats == 0, \
        "estimate beats reached recomp_feed despite Type framing"
    assert probe.state == "LEN_EST", f"feed state {probe.state} != LEN_EST"
    assert len(enc) == 1 and not probe.u_events, \
        "reveals/U-hat appeared without estimates?"


# --------------------------------------------------------------------------
# 1b. diagnosis variant: challenged response bucket-stale
# --------------------------------------------------------------------------
@cocotb.test()
async def test_estimate_ingress_stale_response(dut):
    """The challenged response stamped with a stale bucket never starts a
    challenge at all.

    canon_proc drops it on hdr_bkt_chk, so it is absent from the commitment —
    which then cannot match the digest the ARM marker promised, so the gate
    holds the bucket back and nothing crosses to the enclosure. The feed stays
    ARMED rather than half-armed, which is the property that matters: arming
    is positional, so a slice that never arrives cannot leave the block
    waiting on a capture that will not come."""
    await reset(dut)
    enc = []
    cocotb.start_soon(mac_monitor(dut, "tse1_mtx", enc))
    probe = FeedProbe(dut)

    tokens = [b"\xaa\x01", b"\xbb\x02", b"\xcc\x03"]
    b = await bucket_window(dut, 800)
    # arm for the slice we intend, then stage it a bucket out of date
    resp = canon_req(0x0BADF00D, b"".join(tokens), bucket=b,
                     bkt_diff=rf.BKT_DIFF)
    digest = cm.period({b: [resp]}, BKTS_PER_CERT)
    await mac_drive(dut, "tse0_mrx", eth_wrap(arm_marker(digest, b)))
    stale = canon_req(0x0BADF00D, b"".join(tokens), bucket=b + 7,
                      bkt_diff=rf.BKT_DIFF)                    # wrong bucket
    await mac_drive(dut, "tse0_mrx", eth_wrap(stale))

    for _ in range(3 * (TIMER_END + 1)):
        await RisingEdge(dut.clk)
    dut._log.info("DIAG(stale-response): " + probe.summary())
    assert not enc, f"a withheld slice reached the enclosure: {enc[:2]}"
    assert probe.state == "ARMED", f"feed state {probe.state} != ARMED"
    assert probe.est_beats == 0 and not probe.u_events, \
        "activity on a challenge that never started"
