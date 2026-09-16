"""Commitment + release-gate tests for recomp_ilock_core.sv.

These cover the staging contract independently of the recomputation loop, so
they hold both before and after recomp_feed is reworked for swap-armed capture:

  * the certificate's INWARD is exactly the modelled commitment over the
    packets that crossed canon_proc (commit_model.py);
  * the START (ID = 0) and ARM (ID = 1) control markers are dropped by
    canon_proc — they never reach traffic_commit, so they are absent from that
    commitment, which is what lets the ARM marker carry a digest of the very
    slice it introduces;
  * batch_buffer withholds a bucket whose commitment does not match the digest
    armed by the ARM marker, and releases it when it does.

With one bucket per certificate every bucket closes a certificate, so a slice
is staged, committed, compared and released inside a single bucket.
"""
from struct import pack

import cocotb
from cocotb.triggers import RisingEdge

import commit_model as cm
from test_recomp_ilock_core import (
    TIMER_END,
    BKTS_PER_CERT, CERT_LEN, canon_req, eth_wrap, mac_drive,
    mac_monitor, reset,
)

NONCE_OFF  = 64 + 16            # cert frame: zero header + 4x4 B scalars
INWARD_OFF = 64 + 32            # cert frame: zero header + 32 B of metadata

START_ID = 0                    # nonce marker
ARM_ID   = 1                    # expected-digest marker


def marker(ctrl_id, kcommit=b"", bucket=0):
    """Header-only control packet: ID = ctrl_id, KEY_COMMIT = kcommit.

    Layout matches canon_req: PLD_LEN | BUCKET | ID | REFERENCE | RESERVED |
    KEY_COMMIT (32 B, big-endian, right-aligned like every other field).
    """
    return (pack(">I", 0) + pack(">I", bucket) + pack(">Q", ctrl_id) +
            pack(">Q", 0) + pack(">Q", 0) + kcommit.rjust(32, b"\x00"))


def inward(cert):
    return cert[INWARD_OFF:INWARD_OFF + 32]


def is_cert(frame):
    """Certificates are the only fixed-size frame on port 0 of this length;
    the other egress stream is canon_proc's 64-byte sync packets."""
    return len(frame) == CERT_LEN


async def await_cert(dut, frames, n, timeout=400_000):
    """Return the n-th certificate frame (1-based)."""
    for _ in range(timeout):
        certs = [f for f in frames if is_cert(f)]
        if len(certs) >= n:
            return certs[n - 1]
        await RisingEdge(dut.clk)
    raise AssertionError(f"timeout waiting for certificate {n}")


def packets(frames):
    """Real packets on the enclosure port.

    Zero-length frames are bucket delimiters: batch_buffer re-inserts the empty
    swap beat (OUTPUT_SWAP = 1) and the feed passes it to eth_reframe, which
    frames it. The reworked feed consumes the delimiter instead of forwarding
    it — filtering here keeps these tests about the gate either way.
    """
    return [f for f in frames if f]


def released(dut):
    """batch_buffer's release verdict: drain_sel[1], the bank-valid flag it
    raises only when the gate opens.

    Read from inside the buffer rather than off port 1 because the enclosure
    path is currently wedged: with OUTPUT_SWAP = 1 the delimiter reaches
    eth_reframe through the unmodified feed and stalls it (see the module
    docstring), so no packet can be observed leaving the device. This probe
    isolates the gate decision, which is what these tests are about.
    """
    return bool(int(dut.buffer_chl.drain_sel.value) & 0b10)


async def watch_release(dut, cycles):
    """True if the buffer grants a NEW release within `cycles`.

    Edge-sensitive on purpose: drain_sel[1] stays raised until the next tick
    clears it, so a level check would re-report the previous bucket's release.
    """
    prev = released(dut)
    for _ in range(cycles):
        await RisingEdge(dut.clk)
        now = released(dut)
        if now and not prev:
            return True
        prev = now
    return False


async def stage(dut, pkts):
    for p in pkts:
        await mac_drive(dut, "tse0_mrx", eth_wrap(p))


async def setup(dut):
    """Reset, start both egress monitors, return (port0, port1) frame lists."""
    await reset(dut)
    p0, p1 = [], []
    cocotb.start_soon(mac_monitor(dut, "tse0_mtx", p0))
    cocotb.start_soon(mac_monitor(dut, "tse1_mtx", p1))
    return p0, p1


# --------------------------------------------------------------------------
@cocotb.test()
async def test_inward_matches_model(dut):
    """The certificate's INWARD is the modelled commitment over the staged
    packets — the value a frontend must be able to predict to arm the gate."""
    p0, _ = await setup(dut)

    pkts = [canon_req(2, b"\xa0" * 16, bucket=0),
            canon_req(3, b"\xb1" * 32, bucket=0)]
    await stage(dut, pkts)

    cert = await await_cert(dut, p0, 1)
    want = cm.period({0: pkts}, BKTS_PER_CERT)
    assert inward(cert) == want, (
        f"INWARD {inward(cert).hex()} != model {want.hex()}")


@cocotb.test()
async def test_markers_not_committed(dut):
    """START and ARM markers are dropped by canon_proc: the commitment is the
    same as if they had never been staged.

    This is what breaks the circular dependency — the ARM marker can carry a
    digest of the slice precisely because it is not part of it.
    """
    p0, _ = await setup(dut)

    pkts = [canon_req(2, b"\xc2" * 24, bucket=0),
            canon_req(3, b"\xd3" * 8, bucket=0)]
    await stage(dut, [marker(START_ID, b"\x5a" * 16, bucket=0),
                      pkts[0],
                      marker(ARM_ID, b"\x77" * 32, bucket=0),
                      pkts[1]])

    cert = await await_cert(dut, p0, 1)
    want = cm.period({0: pkts}, BKTS_PER_CERT)       # markers absent
    assert inward(cert) == want, (
        f"markers reached the commitment: INWARD {inward(cert).hex()} "
        f"!= model-without-markers {want.hex()}")


@cocotb.test()
async def test_non_inference_packets_dropped(dut):
    """The recomp ingress admits inference packets only (canon_proc RECOMP=1):
    a packet with ID[0] clear is dropped like a control marker and never
    reaches the commitment.

    This is what makes PLD_LEN token-aligned everywhere downstream, which
    recomp_feed's capture depends on — it releases a payload beat as soon as
    the last accounted token lands, so a payload that is not a whole number of
    tokens would run past tok_total.
    """
    p0, _ = await setup(dut)

    kept = [canon_req(2, b"\x11" * 16, bucket=0),
            canon_req(3, b"\x22" * 8, bucket=0)]
    dropped = canon_req(4, b"\x33" * 12, bucket=0, inf=0)
    await stage(dut, [kept[0], dropped, kept[1]])

    cert = await await_cert(dut, p0, 1)
    want = cm.period({0: kept}, BKTS_PER_CERT)
    assert inward(cert) == want, (
        f"a non-inference packet reached the commitment: INWARD "
        f"{inward(cert).hex()} != model-without-it {want.hex()}")


@cocotb.test()
async def test_start_marker_nonce_reaches_the_certificate(dut):
    """The START marker is dropped, yet its KEY_COMMIT still lands.

    The nonce / expected-digest capture is gated on hdr_fract_chk (a complete
    header arrived), not on the full hdr_chk that drops the packet — that
    split is what lets a marker hand over its field while never being
    committed. Worth pinning, because the marker's ID = 0 now fails *two*
    parts of hdr_id_valid_chk (reserved ID and the inference bit): if either
    ever moved upstream of the latch, the nonce would go silently zero.
    """
    p0, _ = await setup(dut)

    nonce = bytes(range(0x40, 0x50))          # 16 B: the low half of KEY_COMMIT
    await stage(dut, [marker(START_ID, nonce, bucket=0),
                      canon_req(2, b"\x44" * 16, bucket=0)])

    cert = await await_cert(dut, p0, 1)
    got = cert[NONCE_OFF:NONCE_OFF + 16]
    assert got == nonce, f"cert NONCE {got.hex()} != staged {nonce.hex()}"


@cocotb.test()
async def test_gate_withholds_unarmed_bucket(dut):
    """With no ARM marker the expected digest is zero, so the commitment never
    matches and nothing is released toward the enclosure."""
    p0, p1 = await setup(dut)

    await stage(dut, [canon_req(2, b"\xe4" * 16, bucket=0)])

    granted = False
    for _ in range(3):                               # several buckets
        granted |= await watch_release(dut, TIMER_END + 1)
    assert not granted, "unarmed bucket was granted a release"
    assert not packets(p1), (
        f"unarmed bucket reached the enclosure: {len(packets(p1))} packet(s)")


@cocotb.test()
async def test_gate_releases_matching_bucket(dut):
    """An ARM marker carrying the bucket's commitment opens the gate.

    With one bucket per certificate the bucket's close finalises the digest the
    gate compares against, and the comparison runs one grace period later when
    the bank drains — so a slice is armed and released within its own bucket,
    with no period phase for the frontend to track.
    """
    p0, _ = await setup(dut)

    pkts = [canon_req(2, b"\xf5" * 16, bucket=0),
            canon_req(3, b"\x69" * 24, bucket=0)]
    expected = cm.period({0: pkts}, BKTS_PER_CERT)

    await stage(dut, [marker(ARM_ID, expected, bucket=0)] + pkts)

    cert = await await_cert(dut, p0, 1)
    assert inward(cert) == expected, (
        "staging did not produce the armed digest — the gate cannot open: "
        f"INWARD {inward(cert).hex()} != armed {expected.hex()}")

    # the drain, and so the comparison, falls one grace period into the next
    # bucket
    assert await watch_release(dut, TIMER_END + 1), (
        "armed bucket was withheld even though the commitment matched")


@cocotb.test()
async def test_arm_does_not_carry_to_the_next_bucket(dut):
    """One ARM marker releases one bucket, even against identical traffic.

    The expectation register is sticky — canon_proc holds it until another ARM
    marker replaces it — so nothing in the gate itself stops a second bucket
    from matching. What stops it is the bucket stamp: every packet carries
    BUCKET in its header, so re-staging the same payloads a bucket later hashes
    differently and cannot match the digest armed for the first one.
    """
    p0, _ = await setup(dut)

    payloads = [b"\xf5" * 16, b"\x69" * 24]
    first = [canon_req(2, payloads[0], bucket=0), canon_req(3, payloads[1], bucket=0)]
    await stage(dut, [marker(ARM_ID, cm.period({0: first}, BKTS_PER_CERT), bucket=0)]
                     + first)

    # the drain decision lands a grace period into the following bucket, so
    # give it two bucket periods
    assert await watch_release(dut, 2 * (TIMER_END + 1)), "first bucket was withheld"

    # same payloads, next bucket, no new ARM marker — the drain above landed a
    # grace period into bucket 1, so this stages inside it
    again = [canon_req(2, payloads[0], bucket=1), canon_req(3, payloads[1], bucket=1)]
    await stage(dut, again)

    assert not await watch_release(dut, 2 * (TIMER_END + 1)), (
        "a stale arm released a second bucket")
