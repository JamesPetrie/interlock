"""Python model of the traffic commitment hierarchy (verification-protocol.md).

Mirrors leaf_hash -> record_layer -> traffic_commit exactly, so a testbench can
predict the digest the interlock will compute — which is what the frontend has
to put in the ARM marker to open the recomp core's release gate, and what the
certificate carries as INWARD.

    H(payload)  = SHA256(payload bytes)
    H(packet)   = SHA256(header || H(payload))          header = first HDR_BYTES
    record      = PLD_LEN (2 B BE) || H(packet)         34 bytes
    bkt_digest  = SHA256(record_1 || ... || record_k)   SHA256(b"") if empty
    overall     = SHA256(bkt_1 || ... || bkt_NUM_BUCKETS)

PLD_LEN is the payload byte count the block actually hashed (record_layer takes
it from the payload hasher, not from the header field), so a packet whose
header lies about its length still records the true count.
"""
from hashlib import sha256
from struct import pack

HDR_BYTES = 64


def record(pkt: bytes, hdr_bytes: int = HDR_BYTES) -> bytes:
    """One packet's 34-byte record."""
    payload = pkt[hdr_bytes:]
    h_pld = sha256(payload).digest()
    h_pkt = sha256(pkt[:hdr_bytes] + h_pld).digest()
    return pack(">H", len(payload)) + h_pkt


def bkt_digest(pkts, hdr_bytes: int = HDR_BYTES) -> bytes:
    """One bucket's digest over the packets committed in it, in arrival order."""
    return sha256(b"".join(record(p, hdr_bytes) for p in pkts)).digest()


def overall(buckets, hdr_bytes: int = HDR_BYTES) -> bytes:
    """The certificate-period digest: one entry per bucket, empties included.

    `buckets` must hold exactly NUM_BUCKETS entries — the empty ones matter,
    since traffic_commit folds a digest per bucket whether or not it saw
    traffic.
    """
    return sha256(b"".join(bkt_digest(b, hdr_bytes) for b in buckets)).digest()


def period(pkts_by_bucket, num_buckets: int, hdr_bytes: int = HDR_BYTES) -> bytes:
    """overall() for a period described as {bucket_index: [pkts]}."""
    buckets = [pkts_by_bucket.get(i, []) for i in range(num_buckets)]
    return overall(buckets, hdr_bytes)
