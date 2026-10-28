"""The wire format: the one place the byte layout is known.

Everything here follows docs/verification-protocol.md (v6), section "Packet
formats". A packet is a 64-byte header followed by PLD_LEN bytes of payload.
Transmission order, every multi-octet field big-endian:

    offset  field       bytes
    0       PLD_LEN     4     payload length
    4       TYPE        4     0 certificate, 1 nonce, 2 sync, 0xF traffic
    8       DIR         2     0x0000 inbound (request), 0xFFFF outbound (response)
    10      BUCKET      4     the time bucket the packet targets
    14      SLOT        2     the packet's slot within the bucket
    16      REFERENCE   8     ID (DIR ‖ BUCKET ‖ SLOT) of an earlier packet; a root references itself
    24      RESERVED    8     zero   (sync packet: DELTA)
    32      KEY_COMMIT  32    request only; zero in a response   (nonce packet: NONCE in the last 16 bytes)
    64      PAYLOAD     PLD_LEN

The sync and nonce packets are header-only. The certificate packet is an
all-zero header followed by the 160-byte certificate.

Digests, as the interlock computes them (H = SHA-256):

    pld_digest  = H(PAYLOAD)
    pkt_digest  = H(HEADER ‖ pld_digest)
    record      = PLD_LEN[15:0] ‖ pkt_digest                     34 bytes
    bkt_digest  = H(record_1 ‖ record_2 ‖ …)                      H(ε) for an empty bucket
    cert_digest = H(bkt_digest_1 ‖ … ‖ bkt_digest_BKT_NUM)        one per direction

The certificate's AUTH_TAG is HMAC-SHA-256 over its 128-byte body.
"""
from __future__ import annotations

import hashlib
import hmac
import struct
from dataclasses import dataclass, replace
from typing import Iterable, NamedTuple

HEADER_SIZE = 64
CERTIFICATE_SIZE = 160
CERTIFICATE_BODY_SIZE = 128
CERTIFICATE_PACKET_SIZE = HEADER_SIZE + CERTIFICATE_SIZE
FRAME_DATA = 1500                      # one Ethernet frame's DATA field
S_MAX = FRAME_DATA - HEADER_SIZE       # largest payload: the frame minus the header
TOKEN_UNIT = 4                         # bytes per token unit; a payload is a whole number of units
VERSION = 6

TYPE_CERTIFICATE = 0x0
TYPE_NONCE = 0x1
TYPE_SYNC = 0x2
TYPE_TRAFFIC = 0xF

INBOUND = 0x0000                       # request log, toward compute
OUTBOUND = 0xFFFF                      # response log, away from compute
DIRECTIONS = (INBOUND, OUTBOUND)

MAX_BUCKET = (1 << 32) - 1
MAX_SLOT = (1 << 16) - 1
NO_SLOT = 0xFFFF                       # sync: no packet accepted in the bucket
NO_DELTA = (1 << 64) - 1
NO_KEY = bytes(32)                     # KEY_COMMIT field of a response

_HEAD = struct.Struct("!IIHIH")        # PLD_LEN TYPE DIR BUCKET SLOT
_CERT = struct.Struct("!IIII16s32s32s32s32s")
assert _HEAD.size == 16 and _CERT.size == CERTIFICATE_SIZE


def other(direction: int) -> int:
    return OUTBOUND if direction == INBOUND else INBOUND


class Id(NamedTuple):
    """A packet's position, DIR ‖ BUCKET ‖ SLOT, 64 bits."""
    dir: int
    bucket: int
    slot: int

    def pack(self) -> int:
        if self.dir not in DIRECTIONS:
            raise ValueError(f"bad direction {self.dir:#x}")
        if not 0 <= self.bucket <= MAX_BUCKET:
            raise ValueError(f"bucket {self.bucket} out of range")
        if not 0 <= self.slot <= MAX_SLOT:
            raise ValueError(f"slot {self.slot} out of range")
        return (self.dir << 48) | (self.bucket << 16) | self.slot

    @classmethod
    def unpack(cls, value: int) -> "Id":
        return cls(value >> 48, (value >> 16) & MAX_BUCKET, value & MAX_SLOT)

    @property
    def order(self) -> tuple[int, int]:
        """Stream order within a direction."""
        return (self.bucket, self.slot)


def reference_ok(reference: Id, id: Id) -> bool:
    """The REFERENCE rule: not later than the packet. Same direction:
    REFERENCE <= ID; other direction: REFERENCE.BUCKET < BUCKET."""
    if reference.dir not in DIRECTIONS:
        return False
    if reference.dir == id.dir:
        return reference.order <= id.order
    return reference.bucket < id.bucket


class FormatError(ValueError):
    """A packet that does not parse; `reason` names the rule it broke."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


@dataclass(frozen=True)
class Traffic:
    """A request or response packet."""
    id: Id
    reference: Id
    key_commit: bytes = NO_KEY            # H(key material); NO_KEY in a response
    payload: bytes = b""

    @property
    def header(self) -> bytes:
        if len(self.key_commit) != 32:
            raise ValueError("KEY_COMMIT must be 32 bytes")
        return (_HEAD.pack(len(self.payload), TYPE_TRAFFIC, self.id.dir, self.id.bucket, self.id.slot)
                + self.reference.pack().to_bytes(8, "big") + bytes(8) + self.key_commit)

    def pack(self) -> bytes:
        return self.header + self.payload

    @property
    def record(self) -> bytes:
        return record(len(self.payload), pkt_digest(self.header, pld_digest(self.payload)))


@dataclass(frozen=True)
class Sync:
    """Per-bucket feedback from the interlock to one direction's sender:
    the bucket that just closed, the slot of the first packet accepted in it
    and the interlock timer at that header (NO_SLOT / NO_DELTA if none)."""
    dir: int
    bucket: int
    first_slot: int = NO_SLOT
    delta: int = NO_DELTA

    def pack(self) -> bytes:
        return (_HEAD.pack(0, TYPE_SYNC, self.dir, self.bucket, self.first_slot)
                + bytes(8) + self.delta.to_bytes(8, "big") + bytes(32))


@dataclass(frozen=True)
class Nonce:
    """The verifier's nonce, sent inbound by the frontend; the interlock
    latches it and drops the packet."""
    id: Id
    nonce: bytes

    def pack(self) -> bytes:
        if len(self.nonce) != 16:
            raise ValueError("NONCE must be 16 bytes")
        return _HEAD.pack(0, TYPE_NONCE, self.id.dir, self.id.bucket, self.id.slot) + bytes(32) + self.nonce


@dataclass(frozen=True)
class Certificate:
    """The interlock's once-per-BKT_NUM-buckets commitment to both logs."""
    version: int
    device: int
    bkt_start: int
    bkt_num: int
    nonce: bytes
    inward: bytes
    outward: bytes
    chain: bytes
    auth_tag: bytes = bytes(32)

    @property
    def body(self) -> bytes:
        return self.pack()[:CERTIFICATE_BODY_SIZE]

    def pack(self) -> bytes:
        return _CERT.pack(self.version, self.device, self.bkt_start, self.bkt_num,
                          self.nonce, self.inward, self.outward, self.chain, self.auth_tag)

    @classmethod
    def unpack(cls, data: bytes) -> "Certificate":
        if len(data) != CERTIFICATE_SIZE:
            raise FormatError("certificate", f"{len(data)} bytes")
        return cls(*_CERT.unpack(data))

    def tag(self, mac_key: bytes) -> bytes:
        return hmac.new(mac_key, self.body, hashlib.sha256).digest()

    def signed(self, mac_key: bytes) -> "Certificate":
        return replace(self, auth_tag=self.tag(mac_key))

    def verify(self, mac_key: bytes) -> bool:
        return hmac.compare_digest(self.tag(mac_key), self.auth_tag)

    def covers(self, bucket: int) -> bool:
        return self.bkt_start <= bucket < self.bkt_start + self.bkt_num

    def packet(self) -> bytes:
        return bytes(HEADER_SIZE) + self.pack()


Packet = Traffic | Sync | Nonce | Certificate


def frame_length(head: bytes) -> int:
    """Total packet length from its first 8 bytes, for a byte-stream carrier."""
    if len(head) < 8:
        raise FormatError("short")
    pld_len, type_ = struct.unpack_from("!II", head)
    return CERTIFICATE_PACKET_SIZE if type_ == TYPE_CERTIFICATE else HEADER_SIZE + pld_len


def parse(data: bytes) -> Packet:
    """The packet a byte string is, or FormatError with the rule it broke:
    `short`, `length` (PLD_LEN disagrees with the data), `type`, `dir`,
    `reserved`, `certificate`."""
    if len(data) < HEADER_SIZE:
        raise FormatError("short", f"{len(data)} bytes")
    pld_len, type_, dir, bucket, slot = _HEAD.unpack_from(data)
    reference, reserved, tail = data[16:24], data[24:32], data[32:64]
    if type_ == TYPE_CERTIFICATE:
        if data[:HEADER_SIZE] != bytes(HEADER_SIZE) or len(data) != CERTIFICATE_PACKET_SIZE:
            raise FormatError("certificate")
        return Certificate.unpack(data[HEADER_SIZE:])
    if len(data) != HEADER_SIZE + pld_len:
        raise FormatError("length", f"PLD_LEN {pld_len}, {len(data) - HEADER_SIZE} bytes of payload")
    if type_ == TYPE_TRAFFIC:
        if dir not in DIRECTIONS:
            raise FormatError("dir", f"{dir:#06x}")
        if reserved != bytes(8) or (dir == OUTBOUND and tail != NO_KEY):
            raise FormatError("reserved")
        return Traffic(Id(dir, bucket, slot), Id.unpack(int.from_bytes(reference, "big")),
                       tail, data[HEADER_SIZE:])
    if type_ == TYPE_SYNC:
        if pld_len or reference != bytes(8) or tail != bytes(32):
            raise FormatError("reserved")
        return Sync(dir, bucket, slot, int.from_bytes(reserved, "big"))
    if type_ == TYPE_NONCE:
        if pld_len or reference != bytes(8) or reserved != bytes(8) or tail[:16] != bytes(16):
            raise FormatError("reserved")
        return Nonce(Id(dir, bucket, slot), tail[16:])
    raise FormatError("type", f"{type_:#x}")


# --- digests ---------------------------------------------------------------

def pld_digest(payload: bytes) -> bytes:
    return hashlib.sha256(payload).digest()


def pkt_digest(header: bytes, pld_digest_: bytes) -> bytes:
    if len(header) != HEADER_SIZE:
        raise ValueError("pkt_digest needs the 64-byte header")
    return hashlib.sha256(header + pld_digest_).digest()


def record(pld_len: int, pkt_digest_: bytes) -> bytes:
    return (pld_len & 0xFFFF).to_bytes(2, "big") + pkt_digest_


def bkt_digest(records: Iterable[bytes]) -> bytes:
    return hashlib.sha256(b"".join(records)).digest()


def cert_digest(bkt_digests: Iterable[bytes]) -> bytes:
    return hashlib.sha256(b"".join(bkt_digests)).digest()


@dataclass(frozen=True)
class Params:
    """The build parameters both ends and the interlock must agree on."""
    num_slot: int = 64            # slots per bucket
    bkt_num: int = 1000           # buckets per certificate
    bucket_ms: float = 1.0        # bucket length
    device: int = 0               # the interlock's DEVICE id
    s_max: int = S_MAX
    token_unit: int = TOKEN_UNIT

    @property
    def capacity(self) -> int:
        """Per-bucket capacity C in bytes: what one direction can carry per bucket."""
        return self.num_slot * self.s_max
