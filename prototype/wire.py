"""Wire formats and hashing — the one thing every node must agree on byte-for-byte.

Defined once and imported by every node, so the interlock that hashes a packet
and the verifier that checks it can never disagree. Each fixed-width message is a
layout table; pack() and unpack() are generated from the same table.

All integers big-endian. The cipher is a stand-in stream cipher (SHA-256
keystream XOR, domain-separated per direction) so there are no dependencies; the
real system uses AES-CTR. Both are position-addressable, which is all the
protocol relies on. (Recomputation — turning a challenged response into a U
bound — is out of scope here; it's added later as recomputation or a ZKP.)
"""
import hashlib
import hmac as _hmac
import struct
from typing import NamedTuple

__all__ = [
    "UNIT", "TAG", "VERSION", "HEADER", "HDR", "RECORD", "CERT", "Opening",
    "pack", "unpack", "H", "mac", "mac_ok", "encrypt", "decrypt",
    "tokens_to_bytes", "bytes_to_tokens", "input_packet", "output_packet",
    "parse_packet", "packet_hash", "record", "bucket_hash", "overall_hash",
    "cert_body", "cert_body_from_roots", "parse_certificate", "locate",
    "BEACON", "BEACON_MAGIC", "beacon_body", "parse_beacon",
]

UNIT = 4                 # bytes per token on the wire
TAG = 32                 # HMAC-SHA-256 tag size
VERSION = b"ilock-v5"    # 8 bytes


def pack(layout, fields):
    out = b""
    for name, size, kind in layout:
        v = fields[name]
        chunk = v.to_bytes(size, "big") if kind == "int" else v
        assert len(chunk) == size, name
        out += chunk
    return out


def unpack(layout, data):
    out, pos = {}, 0
    for name, size, kind in layout:
        chunk = data[pos:pos + size]
        out[name] = int.from_bytes(chunk, "big") if kind == "int" else chunk
        pos += size
    return out


# Packet = header | ciphertext. recomp_commitment = H(key) = H2 (reserved for the
# future recomputation step; committed now, unused here).
HEADER = {
    "in":  [("length", 4, "int"), ("request_id", 8, "int"),
            ("bucket", 8, "int"), ("recomp_commitment", 32, "bytes")],
    "out": [("length", 4, "int"), ("request_id", 8, "int"),
            ("bucket", 8, "int")],
}
HDR = {d: sum(size for _, size, _ in layout) for d, layout in HEADER.items()}

# Record: what the interlock commits per packet (hashing step 1).
RECORD = [("length", 4, "int"), ("request_id", 8, "int"), ("packet_hash", 32, "bytes")]

# Certificate body m (the HMAC tag follows it on the wire).
CERT = [("version", 8, "bytes"), ("interlock_id", 8, "int"),
        ("bucket_start", 8, "int"), ("num_buckets", 4, "int"),
        ("overall_in", 32, "bytes"), ("overall_out", 32, "bytes"),
        ("nonce", 16, "bytes")]


def H(data):
    return hashlib.sha256(data).digest()


def mac(key, m):
    return _hmac.new(key, m, hashlib.sha256).digest()


def mac_ok(key, m, tag):
    return _hmac.compare_digest(mac(key, m), tag)


def encrypt(key, domain, data):  # stand-in cipher; swap for AES-CTR
    ks = b""
    block = 0
    while len(ks) < len(data):
        ks += H(key + domain + block.to_bytes(8, "big"))
        block += 1
    return bytes(a ^ b for a, b in zip(data, ks))


decrypt = encrypt  # XOR stream cipher is its own inverse


def tokens_to_bytes(tokens):
    return b"".join(t.to_bytes(UNIT, "big") for t in tokens)


def bytes_to_tokens(data):
    return [int.from_bytes(data[i:i + UNIT], "big") for i in range(0, len(data), UNIT)]


def input_packet(request_id, bucket, key, ciphertext):
    return pack(HEADER["in"], {"length": len(ciphertext), "request_id": request_id,
                               "bucket": bucket,
                               "recomp_commitment": H(key)}) + ciphertext


def output_packet(request_id, bucket, ciphertext):
    return pack(HEADER["out"], {"length": len(ciphertext), "request_id": request_id,
                                "bucket": bucket}) + ciphertext


def parse_packet(direction, pkt):
    """-> header fields plus "ciphertext"."""
    p = unpack(HEADER[direction], pkt)
    p["ciphertext"] = pkt[HDR[direction]:]
    return p


def packet_hash(direction, pkt):
    """H(header | H(ciphertext)) — the ciphertext hash is a separable leaf (H1)."""
    return H(pkt[:HDR[direction]] + H(pkt[HDR[direction]:]))


def record(direction, pkt):
    """Hashing step 1, per packet."""
    p = parse_packet(direction, pkt)
    return pack(RECORD, {"length": p["length"], "request_id": p["request_id"],
                         "packet_hash": packet_hash(direction, pkt)})


def bucket_hash(records):
    """Hashing step 2, per bucket: the ordered records (H of empty if none)."""
    return H(b"".join(records))


def overall_hash(bucket_hashes):
    """Hashing step 3, per second: the bucket-hash sequence, one per direction."""
    return H(b"".join(bucket_hashes))


def cert_body_from_roots(interlock_id, bucket_start, num_buckets, overall_in, overall_out, nonce):
    """m, from the already-computed overall roots (what a streaming interlock has)."""
    return pack(CERT, {"version": VERSION, "interlock_id": interlock_id,
                       "bucket_start": bucket_start, "num_buckets": num_buckets,
                       "overall_in": overall_in, "overall_out": overall_out, "nonce": nonce})


def cert_body(interlock_id, bucket_start, hashes_in, hashes_out, nonce):
    """m from the per-bucket hash lists (what the prover recomputes from its log)."""
    return cert_body_from_roots(interlock_id, bucket_start, len(hashes_in),
                                overall_hash(hashes_in), overall_hash(hashes_out), nonce)


def parse_certificate(cert):
    m, tag = cert[:-TAG], cert[-TAG:]
    return {**unpack(CERT, m), "m": m, "tag": tag}


# Tick-beacon body (the MAC frame's DATA, DST 02:..:CB). One-way clock broadcast
# (design A): the prover slaves its clock to this so it can declare buckets that
# match the interlock's. It carries only time (not secret), so it stays out of the
# unexplained-info budget. `bucket` is the absolute index starting at the beacon edge.
BEACON_MAGIC = b"ilbcn-v1"
BEACON = [("magic", 8, "bytes"), ("interlock_id", 8, "int"), ("bucket", 8, "int"),
          ("tick_period_ns", 4, "int"), ("stride", 4, "int")]


def beacon_body(interlock_id, bucket, tick_period_ns, stride):
    return pack(BEACON, {"magic": BEACON_MAGIC, "interlock_id": interlock_id,
                         "bucket": bucket, "tick_period_ns": tick_period_ns, "stride": stride})


def parse_beacon(body):
    """Parse a 32-byte tick-beacon body; raises if the magic is wrong."""
    b = unpack(BEACON, body)
    assert b["magic"] == BEACON_MAGIC, "not a beacon"
    return b


def locate(direction, records, x):
    """Index of the packet covering byte x of a bucket, walking the records by
    cumulative wire size; None means x is past the bucket's content. The frontend
    and the verifier both locate through this one function, so their size
    arithmetic cannot diverge."""
    pos = 0
    for i, rec in enumerate(records):
        size = HDR[direction] + unpack(RECORD, rec)["length"]
        if pos <= x < pos + size:
            return i
        pos += size
    return None


class Opening(NamedTuple):
    """Everything the prover transmits for one challenge. Fields after
    records_out are None when byte x is empty."""
    y: int                    # challenged output bucket
    cert_out: bytes           # certificate covering y
    hashes_out: list          # that second's output bucket hashes
    records_out: list         # records of bucket y
    header_out: bytes = None  # header of the hit packet
    h1_out: bytes = None      # H(ciphertext) of the hit packet
    w: int = None             # bucket of the matching input packet
    cert_in: bytes = None     # certificate covering w
    hashes_in: list = None    # that second's input bucket hashes
    records_in: list = None   # records of bucket w
    header_in: bytes = None   # input header (carries the H2 commitment)
    h1_in: bytes = None       # H(ciphertext) of the input packet
