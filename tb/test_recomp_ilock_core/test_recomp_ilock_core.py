"""cocotb testbench for recomp_ilock_core.sv — MAC-level end-to-end.

Drives CoreTSE MAC-client frames into port 0: an ARM marker carrying the
expected slice digest (dropped by canon_proc, so it holds no position in the
slice), then the challenge slice itself — the challenged response FIRST, then
the context, since arming in recomp_feed is positional. Estimate frames go in
on port 1.

Checks, on port 1: the challenged response leaves only as its sanitized
header (ID alone survives; PLD_LEN, BUCKET and RESERVED are zeroed and the
payload stripped, so the enclosure is never told the answers to the length and
timing estimates), the context verbatim behind it, then the token reveals —
and nothing else, in particular no bucket delimiters (the feed consumes the
swap beats batch_buffer re-inserts).

Checks, on port 0: the certificate's OUTWARD, which in RECOMP mode carries the
release decision in its MSB and {id, U} in its low 128 bits.

Reuses test_recomp_feed's frame builders and bit-exact U model (PYTHONPATH),
and commit_model for the digest the ARM marker has to promise.

Also documents the cert/U pairing gap: cert_build waits for BOTH the traffic
digest and a U value, so no certificate is emitted before a challenge
completes, and only one per challenge — "the certificate will not always
have a valid entropy value" is currently "no certificate without one".
"""
import random
from struct import pack

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly

import commit_model as cm      # the digest the ARM marker must promise
import test_recomp_feed as rf   # frame builders + exact U model

TIMER_END     = 4999            # must match the Makefile -P overrides
BKTS_PER_CERT = 1

SYNC_LEN = 64                   # canonical sync packet (id == CANON_SYNC_ID)
# zero header + cert_msg_t + tau. cert_msg_t = version|interlock_id|
# bucket_start|num_buckets (4x32) + nonce (128) + overall_req|overall_rsp|
# prev_tau (3x256) = 1024 bits = 128 B (cert_build.sv M_BYTES).
CERT_LEN = 64 + 128 + 32


# overall_rsp sits at m offset 64: version|interlock_id|bucket_start|
# num_buckets (4x4 B) + nonce (16 B) = 32 B, then overall_req (32 B).
REQ_OFF = 64 + 32               # cert-frame offset of overall_req (INWARD)
RSP_OFF = 64 + 32 + 32          # cert-frame offset of overall_rsp


def outward(cert):
    """OUTWARD in RECOMP mode (cert_build's g_recomp arm):

        {overall_req_match, 127 x 1'b0, id (64), U (64)}

    i.e. the release decision for this certificate's bucket in the MSB — what
    lets the verifier see that only the bucket whose commitment matched the
    expected digest was released — then the scored response's ID and U.
    Returns (match, id, U).
    """
    o = cert[RSP_OFF: RSP_OFF + 32]
    assert (o[0] & 0x7F) == 0 and int.from_bytes(o[1:16], "big") == 0, \
        f"OUTWARD fill bits set: {o.hex()}"
    return (o[0] >> 7,
            int.from_bytes(o[16:24], "big"),
            int.from_bytes(o[24:32], "big"))


# --------------------------------------------------------------------------
# Ethernet / canonical builders
# --------------------------------------------------------------------------
def eth_wrap(payload):
    """dst + src + LENGTH + payload (+ pad to 46) + 4-byte FCS placeholder
    (present per the CoreTSE contract, not checked by eth_deframe)."""
    body = payload + b"\x00" * max(0, 46 - len(payload))
    return (b"\x02\x00\x00\x00\x00\x02" + b"\x02\x00\x00\x00\x00\x01" +
            pack(">H", len(payload)) + body + b"\x00" * 4)


def canon_req(id_cont, pld, bucket=0, inf=1, bkt_diff=0, ref=0, rsvd=0):
    """Canonical packet for the recomp ingress: only the integrity checks
    apply (full header, bucket match, PLD_LEN, byte count) — ids, reference
    and reserved are replayed original values, unconstrained. bkt_diff rides
    in the last KEY_COMMIT word (= the RSP-parse reserved0 low word
    recomp_feed reads).

    inf defaults set: the recomp ingress admits inference packets only, and
    that is also what makes PLD_LEN token-aligned (hdr_pld_rem_chk), so every
    payload staged here must be a whole number of CANON_TOK_BYTES."""
    pkt_id = (id_cont << 1) | inf     # canon_id_t: inf is ID[0]
    return (pack(">I", len(pld)) + pack(">I", bucket) + pack(">Q", pkt_id) +
            pack(">Q", ref) + pack(">Q", rsvd) +
            b"\x00" * 28 + pack(">I", bkt_diff) + pld)


ARM_ID = 1                      # expected-slice-digest marker


def arm_marker(digest, bucket=0):
    """ARM marker: a header-only ID = 1 packet whose KEY_COMMIT carries the
    expected slice digest.

    canon_proc rejects the reserved IDs (0, 1) on the integrity check and drops
    the packet after latching the field, so the marker never reaches
    traffic_commit or batch_buffer: it is not part of the commitment it
    describes (which is what breaks the circular hash), and it holds no
    position in the slice recomp_feed sees.
    """
    return (pack(">I", 0) + pack(">I", bucket) + pack(">Q", ARM_ID) +
            pack(">Q", 0) + pack(">Q", 0) + digest.rjust(32, b"\x00"))


def san_hdr(pkt_id):
    """The sanitized challenge header recomp_feed forwards in place of the
    challenged response: only ID survives, and the payload is stripped."""
    return b"\x00" * 8 + pack(">Q", pkt_id) + b"\x00" * 48


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


async def mac_drive(dut, pfx, frame):
    """Drive one frame into an MRX bundle (rdy/acpt handshake)."""
    rdy  = getattr(dut, f"{pfx}_rdy")
    acpt = getattr(dut, f"{pfx}_acpt")
    for i, (dat, bv, last) in enumerate(words_of(frame)):
        rdy.value = 1
        getattr(dut, f"{pfx}_sof").value = int(i == 0)
        getattr(dut, f"{pfx}_eof").value = int(last)
        getattr(dut, f"{pfx}_dat").value = dat
        getattr(dut, f"{pfx}_bytevalid").value = bv
        while True:
            await ReadOnly()
            taken = int(acpt.value)
            await RisingEdge(dut.clk)
            if taken:
                break
    rdy.value = 0
    getattr(dut, f"{pfx}_sof").value = 0
    getattr(dut, f"{pfx}_eof").value = 0


async def mac_monitor(dut, pfx, frames):
    """Collect frames from an MTX bundle into `frames` as payload bytes
    (Ethernet header stripped, LENGTH-sliced — drops pad and FCS)."""
    getattr(dut, f"{pfx}_acpt").value = 1
    cur = bytearray()
    while True:
        await ReadOnly()
        if int(getattr(dut, f"{pfx}_rdy").value):
            if int(getattr(dut, f"{pfx}_sof").value):
                cur = bytearray()
            dat = int(getattr(dut, f"{pfx}_dat").value)
            cur += dat.to_bytes(4, "little")
            if int(getattr(dut, f"{pfx}_eof").value):
                bv = int(getattr(dut, f"{pfx}_bytevalid").value)
                if bv:
                    cur = cur[:-bv]
                length = int.from_bytes(cur[12:14], "big")
                frames.append(bytes(cur[14:14 + length]))
        await RisingEdge(dut.clk)


async def wait_frames(dut, frames, n, timeout=400_000, what=""):
    for _ in range(timeout):
        if len(frames) >= n:
            return
        await RisingEdge(dut.clk)
    raise AssertionError(f"timeout waiting for {n} {what} frames "
                         f"(got {len(frames)})")


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


@cocotb.test()
async def test_challenge_end_to_end(dut):
    """Armed challenge slice in port 0 -> sanitized challenge header + context
    + reveals out port 1; estimates in port 1; certificate out port 0 carrying
    the release decision and the exact model U."""
    await reset(dut)

    enc_frames, fe_frames = [], []          # port 1 / port 0 egress payloads
    cocotb.start_soon(mac_monitor(dut, "tse1_mtx", enc_frames))
    cocotb.start_soon(mac_monitor(dut, "tse0_mtx", fe_frames))

    # ---- the slice, response-first: arming is positional, so no other data
    # packet may precede the challenged response ----
    tokens = [rf.rand_bytes(rf.TOK_BYTES, 40 + k) for k in range(5)]
    pld = b"".join(tokens)
    resp = canon_req(0x0BAD_F00D, pld, inf=1, bkt_diff=rf.BKT_DIFF)
    # replayed originals: arbitrary non-consecutive ids, nonzero
    # REFERENCE/RESERVED — all pass with the content checks off. Only the
    # BUCKET field must match.
    ctx = [canon_req(0x1234, rf.rand_bytes(24, 7), ref=0x77, rsvd=0xBEEF),
           canon_req(0x0777, rf.rand_bytes(8, 8), inf=1)]
    slice_pkts = [resp] + ctx

    # the whole slice lives in one bucket, and the gate compares that bucket's
    # commitment against what the ARM marker promised
    digest = cm.period({0: slice_pkts}, BKTS_PER_CERT)
    for pkt in [arm_marker(digest)] + slice_pkts:
        await mac_drive(dut, "tse0_mrx", eth_wrap(pkt))

    # ---- what the enclosure receives: the challenge announced as its
    # sanitized header, then the context verbatim ----
    resp_id = (0x0BAD_F00D << 1) | 1            # the scored response's ID
    await wait_frames(dut, enc_frames, 3, what="forwarded")
    assert enc_frames[0] == san_hdr(resp_id), (
        f"challenge header not sanitized: {enc_frames[0].hex()} "
        f"!= {san_hdr(resp_id).hex()}")
    assert enc_frames[1] == ctx[0] and enc_frames[2] == ctx[1], "ctx mismatch"
    assert resp not in enc_frames, "the challenged response must not be forwarded"

    def certs():
        return [f for f in fe_frames if len(f) == CERT_LEN]

    async def wait_cert(n, what):
        for _ in range(200_000):
            if len(certs()) >= n:
                return certs()[n - 1]
            await RisingEdge(dut.clk)
        raise AssertionError(f"timeout waiting for cert {n} ({what})")

    # ---- estimate loop: len, time, tok_0..N-1; one reveal per non-final
    # token estimate (the last token is scored but never revealed) ----
    acts = rf.challenge_acts(tokens)
    rng = random.Random(123)
    u_model = 0
    for kind, act in acts:
        fb, s = rf.build_frame(kind, act, rng)
        u_model += s
        await mac_drive(dut, "tse1_mrx", eth_wrap(fb))

    await wait_frames(dut, enc_frames, 2 + len(tokens), what="reveal")
    for i, tok in enumerate(tokens[:-1]):
        rev = enc_frames[3 + i]
        # reveal frame = (index big-endian 2B, token wire order)
        assert rev == pack(">H", i) + tok, \
            f"reveal {i}: {rev.hex()} != {pack('>H', i).hex()}{tok.hex()}"

    # ---- the release decision rides the certificate for the bucket it
    # released — the one whose INWARD is the slice commitment ----
    n_now = len(certs())
    cert_slice = await wait_cert(1, "slice")
    assert cert_slice[REQ_OFF:REQ_OFF + 32] == digest, (
        "the first certificate is not the slice's: INWARD "
        f"{cert_slice[REQ_OFF:REQ_OFF + 32].hex()} != {digest.hex()}")
    assert outward(cert_slice)[0] == 1, \
        "the released bucket's certificate does not report the release"

    # ---- U is dispatched only once the estimate loop ends, which is at least
    # a bucket after the drain that released the slice — so it lands in a
    # LATER certificate, one covering an empty (hence unreleased) bucket. The
    # verifier ties the two together through the challenged response's ID and
    # the slice commitment, not through a single record. ----
    for _ in range(200_000):
        got = [outward(f)[1:] for f in certs()]
        if (resp_id, u_model) in got:
            break
        await RisingEdge(dut.clk)
    else:
        raise AssertionError(
            f"no cert with ({resp_id:#x}, {u_model:#x}); saw {got}")

    # ---- let several more buckets tick by: each empty one sends a bare swap
    # beat, which the feed must consume in place. Anything reaching port 1
    # here means a delimiter leaked into eth_reframe (the old wedge) ----
    n_enc = len(enc_frames)
    for _ in range(3 * (TIMER_END + 1)):
        await RisingEdge(dut.clk)
    assert len(enc_frames) == n_enc, (
        f"{len(enc_frames) - n_enc} stray frame(s) toward the enclosure after "
        f"the challenge: {[f.hex() for f in enc_frames[n_enc:]][:4]}")
    assert all(enc_frames), "empty frame toward the enclosure (leaked delimiter)"

    # everything on port 0 must be a sync packet (64 B, id CANON_SYNC_ID) or a
    # certificate
    for f in fe_frames:
        if len(f) == SYNC_LEN:
            assert int.from_bytes(f[8:16], "big") == 1, "unexpected 64B frame"
        else:
            assert len(f) == CERT_LEN, f"unexpected frame len {len(f)}"
    assert len(certs()) > n_now, "the bucket clock stopped producing certificates"


@cocotb.test()
async def test_withheld_slice_never_reaches_the_enclosure(dut):
    """A slice whose commitment does not match the ARM marker's promise is
    withheld: nothing at all crosses to the enclosure — not the challenge, not
    the context, and not a bucket delimiter — and every certificate covering
    those buckets reports the release decision as 'not released'.

    That last part is the point of carrying the decision in OUTWARD: the
    verifier can see that only the bucket whose commitment matched was let
    through, so a mismatch caused by real packet loss is distinguishable from
    one that was released anyway.
    """
    await reset(dut)

    enc_frames, fe_frames = [], []
    cocotb.start_soon(mac_monitor(dut, "tse1_mtx", enc_frames))
    cocotb.start_soon(mac_monitor(dut, "tse0_mtx", fe_frames))

    # armed with a digest nothing will produce
    tokens = [rf.rand_bytes(rf.TOK_BYTES, 90 + k) for k in range(3)]
    resp = canon_req(0x0D15_EA5E, b"".join(tokens), inf=1, bkt_diff=rf.BKT_DIFF)
    ctx  = canon_req(0x2222, rf.rand_bytes(16, 17), ref=0x11)
    for pkt in [arm_marker(b"\xa5" * 32), resp, ctx]:
        await mac_drive(dut, "tse0_mrx", eth_wrap(pkt))

    # let the bucket close, its drain decision pass, and a few more go by
    for _ in range(4 * (TIMER_END + 1)):
        await RisingEdge(dut.clk)

    assert not enc_frames, (
        f"withheld slice reached the enclosure: {len(enc_frames)} frame(s), "
        f"first {enc_frames[0].hex() if enc_frames else ''}")

    released = [o for o in (outward(f) for f in fe_frames
                            if len(f) == CERT_LEN) if o[0]]
    assert not released, f"a certificate reports a release that never happened: {released}"
