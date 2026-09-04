"""The interlock's traffic-commitment hierarchy, recomputed host-side.

Under the pre-bucket build a certificate covered a single packet, so binding a
challenge was a dictionary lookup: hash the retained packet, find the cert whose
`overall` equalled it. That is no longer true. A prod-1ms certificate commits to
`NUM_BUCKETS` (1000) buckets — a whole second of traffic — so no per-packet digest
can ever equal `INWARD`, and the old lookup silently finds nothing.

Binding now means recomputing the hierarchy (traffic_commit.md) from retained
traffic and checking the root against the certificate. That is a stronger
statement: reproducing `INWARD` proves the retained set is *exactly* the traffic
that crossed in that direction during the period, not merely that one packet
appeared in it.

The rules below were derived against silicon (matched on 1- and 3-packet periods):

    record  = SHA256( u16be(len(payload)) || SHA256(header || SHA256(payload)) )
    bucket  = the single record, when a bucket holds one packet
              SHA256(b"") when it holds none
    root    = SHA256( bucket[0] || bucket[1] || ... || bucket[NUM_BUCKETS-1] )

`len(payload)` is the payload bytes as actually forwarded — traffic_commit.md is
explicit that the committed length comes from the block's own streaming counter,
not the header's claimed `pld_len`.

Note the single-record bucket: it is the record *itself*, not SHA256(record) —
a lone Merkle leaf is its own root. This is the case the app always hits, since
it keeps one packet in flight at a time, so each packet lands alone in a bucket.
Multi-packet buckets need the tree's combining rule, which is NOT yet pinned
down against silicon; `bucket_hash` raises rather than guess.
"""
import hashlib
import itertools
import struct

HDR_BYTES = 64
EMPTY_BUCKET = hashlib.sha256(b"").digest()


def record(data: bytes) -> bytes:
    """Leaf digest for one forwarded canonical packet (header || payload)."""
    hdr, pl = data[:HDR_BYTES], data[HDR_BYTES:]
    inner = hashlib.sha256(hdr + hashlib.sha256(pl).digest()).digest()
    return hashlib.sha256(struct.pack(">H", len(pl)) + inner).digest()


def declared_bucket(data: bytes) -> int:
    """The bucket a packet declares. Packets are self-locating: the declared
    bucket in the canonical header is authoritative, never the host's clock."""
    return struct.unpack("!I", data[4:8])[0]


def bucket_hash(packets) -> bytes:
    """Root over the packets that landed in one bucket."""
    if not packets:
        return EMPTY_BUCKET
    if len(packets) == 1:
        return record(packets[0])
    raise NotImplementedError(
        "multi-packet bucket: the tree's combining rule is not yet pinned down "
        "against silicon. The app sends one packet at a time, so this should not "
        "arise; if it does, derive the rule the way commit.py's docstring describes.")


def epoch_root(by_bucket, bkt_start: int, num_buckets: int) -> bytes:
    """Recompute a certificate's INWARD/OUTWARD from retained traffic.

    `by_bucket` maps declared bucket -> list of canonical DATA for that bucket.
    Buckets with nothing retained are treated as empty, so a mismatch means the
    retained set is incomplete (a capture gap) or wrong — which is exactly the
    signal the byte-audit is meant to raise."""
    return hashlib.sha256(b"".join(
        bucket_hash(by_bucket.get(b, [])) for b in range(bkt_start, bkt_start + num_buckets)
    )).digest()


def audit(by_bucket, cert, direction="req") -> bool:
    """True iff retained traffic reproduces the certificate's digest.

    `cert` is a parsed certificate dict (bucket_start, num_buckets, overall_req,
    overall_rsp). `direction` picks INWARD ("req") or OUTWARD ("rsp")."""
    want = cert["overall_req"] if direction == "req" else cert["overall_rsp"]
    return epoch_root(by_bucket, cert["bucket_start"], cert["num_buckets"]) == want


MAX_AMBIGUOUS = 12          # 2^12 hypotheses is ~1 s; real periods carry 0-3


def audit_search(confirmed, ambiguous, cert, direction="req",
                 max_ambiguous=MAX_AMBIGUOUS):
    """Audit a period whose retained set has frames of uncertain status.

    A sender learns a frame was accepted only from FIRST_ARR on the sync that
    closes the bucket it declared. That signal is not guaranteed: if the receive
    path starves for a few milliseconds the sync is dropped by the kernel, and a
    frame the device *did* accept and commit looks, from here, exactly like one it
    rejected. Leaving those frames out of the retained set makes the recomputed
    root disagree with the certificate -- an audit failure that reports as tampering
    when it is really a lost ack.

    `confirmed` maps bucket -> [canonical DATA] for frames the device acknowledged;
    `ambiguous` the same for frames it never acknowledged. The certificate settles
    which is which: recompute the root under each hypothesis and see which one the
    device actually signed.

    Returns `(ok, resolved)` -- `resolved` lists the ambiguous buckets that had to
    be counted as accepted, so `[]` means the plain audit passed and `None` means no
    hypothesis worked. This weakens nothing. The thing checked is still the epoch
    root, so a pass still proves the retained set IS that period's committed
    traffic; all the search does is stop a lost ack from hiding that fact.
    """
    lo, n = cert["bucket_start"], cert["num_buckets"]
    want = cert["overall_req"] if direction == "req" else cert["overall_rsp"]
    # Bucket digests once, then patch: each hypothesis differs in a couple of the
    # 1000 slots, so rebuilding every one of them per trial is pure waste.
    parts = [bucket_hash(by_bucket_get(confirmed, b)) for b in range(lo, lo + n)]
    if hashlib.sha256(b"".join(parts)).digest() == want:
        return True, []
    cand = [b for b in sorted(ambiguous)
            if lo <= b < lo + n and ambiguous[b] and not by_bucket_get(confirmed, b)]
    if not cand or len(cand) > max_ambiguous:
        return False, None
    # Smallest explanation first: a single lost sync is far likelier than five.
    for r in range(1, len(cand) + 1):
        for combo in itertools.combinations(cand, r):
            trial = list(parts)
            for b in combo:
                trial[b - lo] = bucket_hash(ambiguous[b])
            if hashlib.sha256(b"".join(trial)).digest() == want:
                return True, list(combo)
    return False, None


def by_bucket_get(by_bucket, b):
    """`by_bucket[b]` tolerating either a dict or an OrderedDict of lists."""
    return by_bucket.get(b) or []
