"""The software interlock: stands in for the device on the link.

One wire toward the frontend (the inbound port) and one toward compute (the
outbound port). Every packet is checked against the validity rules of
docs/verification-protocol.md as it arrives; what passes is forwarded at
once and its record goes into the current bucket, what fails is dropped and
counted, never forwarded, never hashed. A nonce packet is latched and
dropped.

Buckets tick on the interlock's own clock. When a bucket closes, each
direction's records become its `bkt_digest` and each direction's sender gets
a sync packet: the closed bucket, the slot of the first packet accepted in it
and the timer value at that header. Every BKT_NUM buckets the two runs of
bucket digests become a certificate, chained to the previous one by its tag
and carrying the latched nonce, signed with the MAC key and sent toward the
frontend. Nothing else is stored.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field

from . import packet as P
from .wire import Wire, WireClosed

log = logging.getLogger(__name__)


def _trace_file():
    """ILSTACK_TRACE=<path>: per-packet trace lines go there (use local disk;
    a synchronous write to a network filesystem stalls the loop for
    milliseconds). ILSTACK_TRACE=1 means stderr."""
    path = os.environ.get("ILSTACK_TRACE", "")
    if not path:
        return None
    return sys.stderr if path == "1" else open(path, "a", buffering=1 << 16)


class Ticker:
    """The interlock's clock: buckets of a fixed length from its start."""

    def bucket(self) -> int:
        raise NotImplementedError

    def delta(self, bucket: int) -> int:
        """Timer units since `bucket` started."""
        raise NotImplementedError

    async def wait_for(self, bucket: int) -> None:
        """Return once `bucket` has started."""
        raise NotImplementedError


class RealTicker(Ticker):
    DELTA_UNIT = 1e-6                     # one timer unit: a microsecond

    def __init__(self, bucket_ms: float):
        self.L = bucket_ms / 1000.0
        self.t0 = time.monotonic()

    def bucket(self) -> int:
        return int((time.monotonic() - self.t0) / self.L)

    def delta(self, bucket: int) -> int:
        return max(0, int((time.monotonic() - self.t0 - bucket * self.L) / self.DELTA_UNIT))

    async def wait_for(self, bucket: int) -> None:
        delay = self.t0 + bucket * self.L - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)


@dataclass
class Counters:
    accepted: int = 0
    nonces: int = 0
    dropped: dict = field(default_factory=lambda: defaultdict(int))     # rule -> count

    @property
    def drops(self) -> int:
        return sum(self.dropped.values())


@dataclass
class _Bucket:
    records: list = field(default_factory=list)
    last_slot: int = -1
    first_slot: int = P.NO_SLOT
    delta: int = P.NO_DELTA


class SoftwareInterlock:
    def __init__(self, params: P.Params, frontend: Wire, compute: Wire, mac_key: bytes,
                 ticker: Ticker | None = None):
        self.params = params
        self.wires = {P.INBOUND: frontend, P.OUTBOUND: compute}    # keyed by the direction received on that port
        self.mac_key = mac_key
        self.ticker = ticker or RealTicker(params.bucket_ms)
        self.counters = {d: Counters() for d in P.DIRECTIONS}
        self.nonce = bytes(16)
        self.closed = -1                                          # the last bucket closed
        self.last_tag = bytes(32)                                 # CHAIN for the next certificate
        self.certificates_built = 0
        self._buckets: dict[tuple[int, int], _Bucket] = defaultdict(_Bucket)    # (direction, bucket)
        self._digests: dict[int, list[bytes]] = {d: [] for d in P.DIRECTIONS}  # the open certificate interval
        self._tasks: list[asyncio.Task] = []
        self._trace = _trace_file()                                # per-packet timestamps, for diagnosis

    async def start(self) -> None:
        self._tasks = [asyncio.create_task(self._port(d)) for d in P.DIRECTIONS]
        self._tasks.append(asyncio.create_task(self._ticks()))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        for w in self.wires.values():
            await w.close()

    # --- the ports ------------------------------------------------------------

    async def _port(self, direction: int) -> None:
        wire = self.wires[direction]
        while True:
            try:
                raw = await wire.recv()
            except WireClosed:
                return
            pkt = self.check(direction, raw)
            if pkt is not None:
                await self.wires[P.other(direction)].send(raw)

    def check(self, direction: int, raw: bytes) -> P.Traffic | None:
        """The validity rules, in the spec's order. The accepted packet, or
        None for a drop (counted under the rule it broke)."""
        c = self.counters[direction]
        now = self.ticker.bucket()
        try:
            pkt = P.parse(raw)
        except P.FormatError as e:
            return self._drop(c, "reserved" if e.reason == "reserved" else "header", direction, str(e))
        if isinstance(pkt, P.Nonce):
            if direction != P.INBOUND:
                return self._drop(c, "type", direction, "nonce on the outbound port")
            self.nonce = pkt.nonce
            c.nonces += 1
            return None                                            # latched, never forwarded
        if not isinstance(pkt, P.Traffic):
            return self._drop(c, "type", direction, type(pkt).__name__)
        if len(pkt.payload) > self.params.s_max:
            return self._drop(c, "size", direction, f"PLD_LEN {len(pkt.payload)}")
        if pkt.id.dir != direction:
            return self._drop(c, "dir", direction, f"{pkt.id}")
        if pkt.id.bucket != now:
            return self._drop(c, "bucket", direction, f"{pkt.id} {'late' if pkt.id.bucket < now else 'early'}, now {now}")
        if pkt.id.slot >= self.params.num_slot:
            return self._drop(c, "slot", direction, f"{pkt.id}")
        b = self._buckets[(direction, now)]
        if pkt.id.slot <= b.last_slot:
            return self._drop(c, "order", direction, f"{pkt.id} after slot {b.last_slot}")
        if not P.reference_ok(pkt.reference, pkt.id):
            return self._drop(c, "reference", direction, f"{pkt.id} -> {pkt.reference}")
        if len(pkt.payload) % self.params.token_unit:
            return self._drop(c, "units", direction, f"PLD_LEN {len(pkt.payload)}")
        if b.first_slot == P.NO_SLOT:
            b.first_slot, b.delta = pkt.id.slot, self.ticker.delta(now)
        if self._trace:
            print(f"TRACE check {pkt.id.pack()} {time.monotonic():.6f} {self.ticker.delta(now)}", file=self._trace)
        b.last_slot = pkt.id.slot
        b.records.append(pkt.record)
        c.accepted += 1
        return pkt

    def _drop(self, c: Counters, rule: str, direction: int, detail: str) -> None:
        c.dropped[rule] += 1
        if self._trace:
            print(f"TRACE drop {rule} {'inbound' if direction == P.INBOUND else 'outbound'}: {detail}", file=self._trace)
        return None

    # --- buckets and certificates --------------------------------------------

    async def _ticks(self) -> None:
        while True:
            await self.ticker.wait_for(self.closed + 2)             # bucket closed+1 is over
            while self.closed + 1 < self.ticker.bucket():
                await self.close_bucket(self.closed + 1)

    async def close_bucket(self, bucket: int) -> None:
        self.closed = bucket
        for d in P.DIRECTIONS:
            b = self._buckets.pop((d, bucket), _Bucket())
            self._digests[d].append(P.bkt_digest(b.records))
            if self._trace and os.environ.get("ILSTACK_TRACE_SYNCS"):
                print(f"TRACE sync {d} {bucket} {time.monotonic():.6f}", file=self._trace)
            await self.wires[d].send(P.Sync(d, bucket, b.first_slot, b.delta).pack())
        if (bucket + 1) % self.params.bkt_num == 0:
            await self._certify(bucket + 1 - self.params.bkt_num)

    async def _certify(self, bkt_start: int) -> None:
        cert = P.Certificate(P.VERSION, self.params.device, bkt_start, self.params.bkt_num, self.nonce,
                             P.cert_digest(self._digests[P.INBOUND]), P.cert_digest(self._digests[P.OUTBOUND]),
                             self.last_tag).signed(self.mac_key)
        self._digests = {d: [] for d in P.DIRECTIONS}
        self.last_tag = cert.auth_tag
        self.certificates_built += 1
        await self.wires[P.INBOUND].send(cert.packet())
