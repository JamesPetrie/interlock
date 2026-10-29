"""The challenge flow: what the prover opens and what the verifier checks.

Following docs/verification-protocol.md, "Challenge":

1. Anchor. The verifier sends a fresh nonce; the prover returns the next
   certificate echoing it. The verifier logs the anchor and checks that the
   bucket counter moves forward and at the bucket rate against its own
   clock.
2. Select. Uniform (bucket y, byte x) over [B - window, B] x [0, C), with B
   the latest certified bucket and C the per-bucket capacity, over the
   response log.
3. Open. The prover sends the certificate covering y, that direction's
   BKT_NUM bucket digests, bucket y's records (PLD_LEN and pkt_digest per
   packet), the hit packet's header and pld_digest, and the same for the
   packet the hit references. If the bucket's records do not reach byte x,
   the records alone prove emptiness.
4. Recompute. Not implemented here.
5. Verify, in order: (a) the certificates' tags and DEVICE; (b) the bucket
   digests recompute the certificate's commitment; (c) the records recompute
   the bucket digest; (d) byte x falls in the claimed packet and the packet
   hashes to its record; (e) input binding: the referenced packet opens the
   same way, sits where the REFERENCE says, and is earlier.

Only headers and digests travel; no payload ever does.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field

from . import packet as P
from .log import Log


@dataclass
class PacketOpening:
    """One packet (or the proof that a byte is beyond a bucket's content)."""
    dir: int
    bucket: int
    certificate: P.Certificate
    bkt_digests: list[bytes]            # the certificate interval, this direction
    pld_lens: list[int]                 # bucket's records, in slot order
    pkt_digests: list[bytes]
    index: int | None = None            # which record is the packet; None: the bucket has no such byte
    header: bytes | None = None
    pld_digest: bytes | None = None

    @property
    def records(self) -> list[bytes]:
        return [P.record(n, d) for n, d in zip(self.pld_lens, self.pkt_digests)]

    def to_json(self) -> dict:
        return {"dir": self.dir, "bucket": self.bucket, "certificate": self.certificate.pack().hex(),
                "bkt_digests": [d.hex() for d in self.bkt_digests], "pld_lens": self.pld_lens,
                "pkt_digests": [d.hex() for d in self.pkt_digests], "index": self.index,
                "header": None if self.header is None else self.header.hex(),
                "pld_digest": None if self.pld_digest is None else self.pld_digest.hex()}

    @classmethod
    def from_json(cls, d: dict) -> "PacketOpening":
        return cls(d["dir"], d["bucket"], P.Certificate.unpack(bytes.fromhex(d["certificate"])),
                   [bytes.fromhex(x) for x in d["bkt_digests"]], list(d["pld_lens"]),
                   [bytes.fromhex(x) for x in d["pkt_digests"]], d["index"],
                   None if d["header"] is None else bytes.fromhex(d["header"]),
                   None if d["pld_digest"] is None else bytes.fromhex(d["pld_digest"]))


@dataclass
class Opening:
    dir: int
    bucket: int
    byte: int
    hit: PacketOpening
    reference: PacketOpening | None = None     # absent for a root (it references itself) or an empty proof

    def to_json(self) -> dict:
        return {"dir": self.dir, "bucket": self.bucket, "byte": self.byte, "hit": self.hit.to_json(),
                "reference": None if self.reference is None else self.reference.to_json()}

    @classmethod
    def from_json(cls, d: dict) -> "Opening":
        return cls(d["dir"], d["bucket"], d["byte"], PacketOpening.from_json(d["hit"]),
                   None if d["reference"] is None else PacketOpening.from_json(d["reference"]))


# --- prover side -------------------------------------------------------------

def _certificate_for(log: Log, bucket: int) -> P.Certificate | None:
    return next((c for c in log.certificates if c.covers(bucket)), None)


def _bucket_opening(params: P.Params, log: Log, direction: int, bucket: int) -> PacketOpening | None:
    cert = _certificate_for(log, bucket)
    if cert is None:
        return None
    digests = [P.bkt_digest(P.parse(raw).record for raw in log.bucket(direction, b))
               for b in range(cert.bkt_start, cert.bkt_start + cert.bkt_num)]
    pkts = [P.parse(raw) for raw in log.bucket(direction, bucket)]
    return PacketOpening(direction, bucket, cert, digests, [len(p.payload) for p in pkts],
                         [P.pkt_digest(p.header, P.pld_digest(p.payload)) for p in pkts])


def _open_packet(params: P.Params, log: Log, id: P.Id) -> PacketOpening | None:
    op = _bucket_opening(params, log, id.dir, id.bucket)
    raw = log.get(id)
    if op is None or raw is None:
        return None
    pkts = [P.parse(r) for r in log.bucket(id.dir, id.bucket)]
    op.index = next(i for i, p in enumerate(pkts) if p.id == id)
    op.header, op.pld_digest = raw[:P.HEADER_SIZE], P.pld_digest(raw[P.HEADER_SIZE:])
    return op


def build_opening(params: P.Params, log: Log, direction: int, bucket: int, byte: int) -> Opening | None:
    """What the prover answers a challenge with; None if no certificate covers the bucket."""
    op = _bucket_opening(params, log, direction, bucket)
    if op is None:
        return None
    start = 0
    for i, n in enumerate(op.pld_lens):
        if start <= byte < start + n:
            op.index = i
            break
        start += n
    if op.index is None:
        return Opening(direction, bucket, byte, op)
    raw = log.bucket(direction, bucket)[op.index]
    pkt = P.parse(raw)
    op.header, op.pld_digest = pkt.header, P.pld_digest(pkt.payload)
    ref = None if pkt.reference == pkt.id else _open_packet(params, log, pkt.reference)
    return Opening(direction, bucket, byte, op, ref)


def audit(params: P.Params, log: Log, cert: P.Certificate, directions: tuple = P.DIRECTIONS) -> dict:
    """The prover's byte-audit of a certificate against its own log: does the
    log reproduce INWARD and OUTWARD? A mismatch means the interlock dropped
    (or saw differently) a packet in that interval which the log still holds.
    `directions` limits the audit to one log when only that one is kept."""
    result = {}
    digest_of: dict[bytes, bytes] = {}                             # payload digests, shared by identical payloads
    for name, direction in (("inward", P.INBOUND), ("outward", P.OUTBOUND)):
        if direction not in directions:
            continue
        digests = []
        for b in range(cert.bkt_start, cert.bkt_start + cert.bkt_num):
            records = log.records(direction, b)
            if records is None:
                records = []
                for raw in log.bucket(direction, b):
                    payload = raw[P.HEADER_SIZE:]
                    pd = digest_of.get(payload)
                    if pd is None:
                        pd = digest_of[payload] = P.pld_digest(payload)
                    records.append(P.record(len(payload), P.pkt_digest(raw[:P.HEADER_SIZE], pd)))
            digests.append(P.bkt_digest(records))
        result[name] = P.cert_digest(digests) == getattr(cert, name)
    return result


# --- verifier side -----------------------------------------------------------

@dataclass
class Anchor:
    nonce: bytes
    certificate: P.Certificate
    t_sent: float
    t_received: float


@dataclass
class Verifier:
    mac_key: bytes
    params: P.Params
    anchors: list[Anchor] = field(default_factory=list)

    # 1. anchor
    def anchor(self, nonce: bytes, cert: P.Certificate, t_sent: float, t_received: float) -> dict:
        checks = {"tag": cert.verify(self.mac_key), "device": cert.device == self.params.device,
                  "nonce": cert.nonce == nonce, "version": cert.version == P.VERSION}
        if self.anchors:
            prev = self.anchors[-1]
            checks["monotonic"] = cert.bkt_start > prev.certificate.bkt_start
            elapsed = t_received - prev.t_received
            buckets = cert.bkt_start - prev.certificate.bkt_start
            slack = self.params.bkt_num * self.params.bucket_ms / 1000.0 + 0.1 * elapsed
            checks["rate"] = abs(buckets * self.params.bucket_ms / 1000.0 - elapsed) <= slack
        if all(checks.values()):
            self.anchors.append(Anchor(nonce, cert, t_sent, t_received))
        return {"ok": all(checks.values()), "checks": checks}

    @staticmethod
    def new_nonce() -> bytes:
        return secrets.token_bytes(16)

    # 2. select
    def select(self, window: int, direction: int = P.OUTBOUND, floor: int | None = None) -> tuple[int, int, int]:
        """(direction, bucket, byte): uniform over the last `window` certified
        buckets and the capacity. `floor`: the first bucket the prover holds
        a certificate for (it keeps a bounded history); nothing older is
        asked for."""
        if not self.anchors:
            raise RuntimeError("anchor first")
        latest = self.anchors[-1].certificate
        top = latest.bkt_start + latest.bkt_num - 1
        low = max(0, top - window, floor if floor is not None else 0)
        return direction, low + secrets.randbelow(top - low + 1), secrets.randbelow(self.params.capacity)

    # 5. verify
    def check(self, op: Opening) -> dict:
        checks = {}
        self._check_packet(checks, "", op.hit, op.dir, op.bucket)
        hit = op.hit
        if hit.index is None:                                   # the records say the byte is beyond the content
            checks["empty"] = sum(hit.pld_lens) <= op.byte
            return self._result(checks, None)
        start = sum(hit.pld_lens[:hit.index]) if hit.index < len(hit.pld_lens) else -1
        checks["byte_in_packet"] = 0 <= start <= op.byte < start + hit.pld_lens[hit.index]
        pkt = _header(hit.header)
        checks["header"] = pkt is not None and pkt.id == P.Id(op.dir, op.bucket, pkt.id.slot) \
            and len(pkt.payload) == 0 and _pld_len(hit.header) == hit.pld_lens[hit.index]
        if pkt is None:
            return self._result(checks, None)
        if pkt.reference == pkt.id:
            checks["reference"] = op.reference is None          # a root: nothing more to open
            return self._result(checks, pkt)
        ref = op.reference
        checks["reference"] = ref is not None and ref.index is not None and P.reference_ok(pkt.reference, pkt.id)
        if ref is not None and ref.index is not None:
            self._check_packet(checks, "ref_", ref, pkt.reference.dir, pkt.reference.bucket)
            rp = _header(ref.header)
            checks["ref_header"] = (rp is not None and rp.id == pkt.reference and ref.index < len(ref.pld_lens)
                                    and _pld_len(ref.header) == ref.pld_lens[ref.index])
            if rp is not None and rp.id.dir == P.INBOUND:
                checks["ref_key_commit"] = rp.key_commit != P.NO_KEY
        return self._result(checks, pkt)

    def _check_packet(self, checks: dict, prefix: str, po: PacketOpening, direction: int, bucket: int) -> None:
        cert = po.certificate
        checks[prefix + "certificate"] = (cert.verify(self.mac_key) and cert.device == self.params.device
                                          and cert.covers(bucket) and po.dir == direction and po.bucket == bucket)
        committed = cert.outward if direction == P.OUTBOUND else cert.inward
        checks[prefix + "cert_digest"] = len(po.bkt_digests) == cert.bkt_num and P.cert_digest(po.bkt_digests) == committed
        i = bucket - cert.bkt_start
        checks[prefix + "bkt_digest"] = (len(po.pld_lens) == len(po.pkt_digests) and 0 <= i < len(po.bkt_digests)
                                         and P.bkt_digest(po.records) == po.bkt_digests[i])
        if po.index is not None:
            checks[prefix + "pkt_digest"] = (po.header is not None and po.pld_digest is not None
                                             and po.index < len(po.pkt_digests)
                                             and P.pkt_digest(po.header, po.pld_digest) == po.pkt_digests[po.index])

    @staticmethod
    def _result(checks: dict, pkt: P.Traffic | None) -> dict:
        return {"verified": all(checks.values()), "checks": checks,
                "id": None if pkt is None else pkt.id, "reference": None if pkt is None else pkt.reference}


def _header(header: bytes | None) -> P.Traffic | None:
    """A header parsed as a traffic packet with an empty payload, or None."""
    if header is None or len(header) != P.HEADER_SIZE:
        return None
    try:
        pkt = P.parse(bytes(4) + header[4:])                   # PLD_LEN zeroed: the header alone, no payload
    except P.FormatError:
        return None
    return pkt if isinstance(pkt, P.Traffic) else None


def _pld_len(header: bytes) -> int:
    return int.from_bytes(header[:4], "big")


def now() -> float:
    return time.time()
