"""The prover's record: every packet in both directions, every certificate,
and the key material, kept for the challenge window.

Packets are stored by ID, exact bytes. Since the interlock accepts a packet
only in the bucket its header names, a log keyed by ID reproduces the
interlock's packet-to-bucket assignment, and a bucket's records in slot
order are what its `bkt_digest` was computed over. What the log cannot know
on its own is whether the interlock dropped a packet this node sent: the
sync feedback names some of those, which are removed again, and the
certificate audit catches the rest.

`MemoryLog` keeps everything in process. `FileLog` appends to files and
rebuilds its index when reopened.
"""
from __future__ import annotations

import os
from collections import deque

from . import packet as P
from .crypto import Pair


class Log:
    certificates: list[P.Certificate]

    def record(self, raw: bytes, id: P.Id | None = None) -> P.Id:
        """Store a traffic packet; `id` saves the parse when the caller built it."""
        raise NotImplementedError

    def get(self, id: P.Id) -> bytes | None:
        raise NotImplementedError

    def remove(self, id: P.Id) -> None:
        """Forget a packet the interlock did not accept."""
        raise NotImplementedError

    def bucket(self, direction: int, bucket: int) -> list[bytes]:
        """The bucket's packets in slot order."""
        raise NotImplementedError

    def certificate(self, cert: P.Certificate) -> None:
        raise NotImplementedError

    def key(self, pair: Pair) -> None:
        raise NotImplementedError

    def counter(self, key_commit: bytes) -> int | None:
        raise NotImplementedError

    def records(self, direction: int, bucket: int) -> list[bytes] | None:
        """The bucket's records (PLD_LEN, packet digest) in slot order, when
        the log keeps them; None means compute them from the packets."""
        return None

    def close(self) -> None:
        pass


class MemoryLog(Log):
    """Packets kept per bucket, in one small dictionary each: a bucket's
    entries come and go as a unit, so no dictionary ever grows large or
    rehashes (a single dictionary of every packet rehashes for milliseconds
    at each doubling, in the middle of a sender's deadline). Every packet's
    record (PLD_LEN, packet digest) is computed as it is logged, with the
    payload digests shared by identical payloads, so a certificate audit is
    a thousand bucket digests and never pauses a sender for long."""

    def __init__(self):
        self._packets: dict[tuple[int, int], dict[int, bytes]] = {}   # (direction, bucket) -> slot -> packet
        self._records: dict[tuple[int, int], dict[int, bytes]] = {}   # (direction, bucket) -> slot -> record
        self._pld: dict[bytes, bytes] = {}                            # payload -> digest, a few recent ones
        self.certificates = []
        self._keys: dict[bytes, int] = {}

    def _id(self, raw: bytes, id: P.Id | None) -> P.Id:
        if id is not None:
            return id
        pkt = P.parse(raw)
        if not isinstance(pkt, P.Traffic):
            raise ValueError("only traffic packets are logged")
        return pkt.id

    def _keep_record(self, raw: bytes, id: P.Id) -> None:
        payload = raw[P.HEADER_SIZE:]
        pd = self._pld.get(payload)
        if pd is None:
            if len(self._pld) >= 64:
                self._pld.clear()
            pd = self._pld[payload] = P.pld_digest(payload)
        self._records.setdefault((id.dir, id.bucket), {})[id.slot] = P.record(len(payload), P.pkt_digest(raw[:P.HEADER_SIZE], pd))

    def _drop_record(self, id: P.Id) -> None:
        b = self._records.get((id.dir, id.bucket))
        if b is not None:
            b.pop(id.slot, None)
            if not b:
                del self._records[(id.dir, id.bucket)]

    def record(self, raw: bytes, id: P.Id | None = None) -> P.Id:
        id = self._id(raw, id)
        self._packets.setdefault((id.dir, id.bucket), {})[id.slot] = raw   # an ID is logged once; the last record wins
        self._keep_record(raw, id)
        return id

    def get(self, id: P.Id) -> bytes | None:
        b = self._packets.get((id.dir, id.bucket))
        return b.get(id.slot) if b else None

    def remove(self, id: P.Id) -> None:
        b = self._packets.get((id.dir, id.bucket))
        if b is not None:
            b.pop(id.slot, None)
            if not b:
                del self._packets[(id.dir, id.bucket)]
        self._drop_record(id)

    def bucket(self, direction: int, bucket: int) -> list[bytes]:
        b = self._packets.get((direction, bucket), {})
        return [b[s] for s in sorted(b)]

    def records(self, direction: int, bucket: int) -> list[bytes]:
        b = self._records.get((direction, bucket), {})
        return [b[s] for s in sorted(b)]

    def certificate(self, cert: P.Certificate) -> None:
        self.certificates.append(cert)

    def key(self, pair: Pair) -> None:
        self._keys[pair.commit] = pair.counter

    def counter(self, key_commit: bytes) -> int | None:
        return self._keys.get(key_commit)


class FileLog(MemoryLog):
    """`packets.bin`: packets appended as sent or received (self-delimiting);
    `dropped.bin`: IDs of packets removed again, 8 bytes each;
    `certificates.bin`: 160-byte certificates; `keys.bin`: KEY_COMMIT and
    counter, 40 bytes each. The packet index stays in memory, per bucket;
    packet bytes are read back through the same descriptor they were written
    with: on a network filesystem a second descriptor may not see them yet."""

    def __init__(self, directory: str):
        super().__init__()
        os.makedirs(directory, exist_ok=True)
        self._ppath = os.path.join(directory, "packets.bin")
        self._cpath = os.path.join(directory, "certificates.bin")
        self._kpath = os.path.join(directory, "keys.bin")
        self._dpath = os.path.join(directory, "dropped.bin")
        self._offsets: dict[tuple[int, int], dict[int, tuple[int, int]]] = {}   # (direction, bucket) -> slot -> (offset, length)
        self._load()
        self._pf = open(self._ppath, "a+b")
        self._cf = open(self._cpath, "ab")
        self._kf = open(self._kpath, "ab")
        self._df = open(self._dpath, "ab")

    def _load(self) -> None:
        if os.path.exists(self._ppath):
            with open(self._ppath, "rb") as f:
                off = 0
                while True:
                    head = f.read(8)
                    if len(head) < 8:
                        break
                    n = P.frame_length(head)
                    raw = head + f.read(n - 8)
                    if len(raw) < n:
                        break
                    self._index(P.parse(raw), raw, off, n)
                    off += n
        if os.path.exists(self._dpath):
            with open(self._dpath, "rb") as f:
                while len(d := f.read(8)) == 8:
                    self._forget(P.Id.unpack(int.from_bytes(d, "big")))
        if os.path.exists(self._cpath):
            with open(self._cpath, "rb") as f:
                while len(c := f.read(P.CERTIFICATE_SIZE)) == P.CERTIFICATE_SIZE:
                    self.certificates.append(P.Certificate.unpack(c))
        if os.path.exists(self._kpath):
            with open(self._kpath, "rb") as f:
                while len(k := f.read(40)) == 40:
                    self._keys[k[:32]] = int.from_bytes(k[32:], "big")

    def _index(self, pkt: P.Traffic, raw: bytes, off: int, n: int) -> None:
        self._index_id(pkt.id, off, n)
        self._keep_record(raw, pkt.id)

    def _index_id(self, id: P.Id, off: int, n: int) -> None:
        self._offsets.setdefault((id.dir, id.bucket), {})[id.slot] = (off, n)   # an ID is logged once; the last record wins

    def record(self, raw: bytes, id: P.Id | None = None) -> P.Id:
        id = self._id(raw, id)
        self._pf.seek(0, os.SEEK_END)
        self._index_id(id, self._pf.tell(), len(raw))
        self._keep_record(raw, id)
        self._pf.write(raw)
        self._pf.flush()
        return id

    def get(self, id: P.Id) -> bytes | None:
        b = self._offsets.get((id.dir, id.bucket))
        loc = b.get(id.slot) if b else None
        if loc is None:
            return None
        self._pf.flush()
        self._pf.seek(loc[0])
        return self._pf.read(loc[1])

    def _forget(self, id: P.Id) -> bool:
        b = self._offsets.get((id.dir, id.bucket))
        if b is None or id.slot not in b:
            return False
        del b[id.slot]
        if not b:
            del self._offsets[(id.dir, id.bucket)]
        self._drop_record(id)
        return True

    def remove(self, id: P.Id) -> None:
        if self._forget(id):
            self._df.write(id.pack().to_bytes(8, "big"))
            self._df.flush()

    def bucket(self, direction: int, bucket: int) -> list[bytes]:
        b = self._offsets.get((direction, bucket), {})
        return [self.get(P.Id(direction, bucket, s)) for s in sorted(b)]

    def certificate(self, cert: P.Certificate) -> None:
        self.certificates.append(cert)
        self._cf.write(cert.pack())
        self._cf.flush()

    def key(self, pair: Pair) -> None:
        self._keys[pair.commit] = pair.counter
        self._kf.write(pair.commit + pair.counter.to_bytes(8, "big"))
        self._kf.flush()

    def __len__(self) -> int:
        return sum(len(b) for b in self._offsets.values())

    def close(self) -> None:
        for f in (self._pf, self._cf, self._kf, self._df):
            f.close()


class RingLog(MemoryLog):
    """A log that keeps only the last `keep` buckets per direction: enough to
    audit certificates as they arrive, for long runs that could not hold
    every packet. The certificates themselves are kept."""

    def __init__(self, keep: int = 3000):
        super().__init__()
        self.keep = keep
        self._order: dict[int, deque] = {}                        # per direction: bucket numbers in arrival order
        self.evicted = 0

    def record(self, raw: bytes, id: P.Id | None = None) -> P.Id:
        id = super().record(raw, id)
        order = self._order.setdefault(id.dir, deque())
        if not order or id.bucket > order[-1]:
            order.append(id.bucket)
            floor = id.bucket - self.keep
            while order and order[0] < floor:
                b = order.popleft()
                gone = self._packets.pop((id.dir, b), None)
                self._records.pop((id.dir, b), None)
                self.evicted += len(gone) if gone else 0
        return id
