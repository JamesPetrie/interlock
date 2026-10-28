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
from collections import defaultdict

from . import packet as P
from .crypto import Pair


class Log:
    certificates: list[P.Certificate]

    def record(self, raw: bytes) -> P.Traffic:
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

    def close(self) -> None:
        pass


class MemoryLog(Log):
    def __init__(self):
        self._packets: dict[P.Id, bytes] = {}
        self._buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
        self.certificates = []
        self._keys: dict[bytes, int] = {}

    def record(self, raw: bytes) -> P.Traffic:
        pkt = P.parse(raw)
        if not isinstance(pkt, P.Traffic):
            raise ValueError("only traffic packets are logged")
        self._packets[pkt.id] = raw
        self._buckets[(pkt.id.dir, pkt.id.bucket)].append(pkt.id.slot)
        return pkt

    def get(self, id: P.Id) -> bytes | None:
        return self._packets.get(id)

    def remove(self, id: P.Id) -> None:
        if self._packets.pop(id, None) is not None:
            self._buckets[(id.dir, id.bucket)].remove(id.slot)

    def bucket(self, direction: int, bucket: int) -> list[bytes]:
        return [self._packets[P.Id(direction, bucket, s)] for s in sorted(self._buckets.get((direction, bucket), ()))]

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
    counter, 40 bytes each. The packet index stays in memory; packet bytes
    are read back from the file."""

    def __init__(self, directory: str):
        super().__init__()
        os.makedirs(directory, exist_ok=True)
        self._ppath = os.path.join(directory, "packets.bin")
        self._cpath = os.path.join(directory, "certificates.bin")
        self._kpath = os.path.join(directory, "keys.bin")
        self._dpath = os.path.join(directory, "dropped.bin")
        self._offsets: dict[P.Id, tuple[int, int]] = {}
        self._load()
        self._pf = open(self._ppath, "ab")
        self._cf = open(self._cpath, "ab")
        self._kf = open(self._kpath, "ab")
        self._df = open(self._dpath, "ab")
        self._rf = open(self._ppath, "rb")

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
                    self._index(P.parse(raw), off, n)
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

    def _index(self, pkt: P.Traffic, off: int, n: int) -> None:
        self._offsets[pkt.id] = (off, n)
        self._buckets[(pkt.id.dir, pkt.id.bucket)].append(pkt.id.slot)

    def record(self, raw: bytes) -> P.Traffic:
        pkt = P.parse(raw)
        if not isinstance(pkt, P.Traffic):
            raise ValueError("only traffic packets are logged")
        self._index(pkt, self._pf.tell(), len(raw))
        self._pf.write(raw)
        self._pf.flush()
        return pkt

    def get(self, id: P.Id) -> bytes | None:
        loc = self._offsets.get(id)
        if loc is None:
            return None
        self._rf.seek(loc[0])
        return self._rf.read(loc[1])

    def _forget(self, id: P.Id) -> None:
        if self._offsets.pop(id, None) is not None:
            self._buckets[(id.dir, id.bucket)].remove(id.slot)

    def remove(self, id: P.Id) -> None:
        if id in self._offsets:
            self._forget(id)
            self._df.write(id.pack().to_bytes(8, "big"))
            self._df.flush()

    def bucket(self, direction: int, bucket: int) -> list[bytes]:
        return [self.get(P.Id(direction, bucket, s)) for s in sorted(self._buckets.get((direction, bucket), ()))]

    def certificate(self, cert: P.Certificate) -> None:
        self.certificates.append(cert)
        self._cf.write(cert.pack())
        self._cf.flush()

    def key(self, pair: Pair) -> None:
        self._keys[pair.commit] = pair.counter
        self._kf.write(pair.commit + pair.counter.to_bytes(8, "big"))
        self._kf.flush()

    def __len__(self) -> int:
        return len(self._offsets)

    def close(self) -> None:
        for f in (self._pf, self._cf, self._kf, self._df, self._rf):
            f.close()
