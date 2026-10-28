"""The dispatcher: payloads in, packets out; packets in, payloads out. It
owns IDs, timing and the log, and knows nothing about what payloads mean.

Sending: a message's payloads become a chain of packets in this node's
direction. The first references what the caller names (itself for a root,
the request's last packet for a response, the previous turn's last packet
for a follow-up), each later one its predecessor. One sender task stamps
each packet just before it leaves: the bucket the clock says is reachable
(later than a referenced packet of the other direction), the next slot in
that bucket, rolling over at NUM_SLOT; then it waits for that bucket's
transmit time and writes. Stamping at departure keeps a backlog from
producing stale buckets, and a single writer keeps IDs in order.

Receiving: traffic packets must arrive in increasing ID order within the
direction, as the interlock guarantees; anything else is dropped and counted.
They are logged and handed over as they are, with IDs and references, since
only the holder of the keys can tell where a message ends. Sync packets
drive the clock and reveal lost packets: a bucket this node sent into whose
sync reports nothing accepted, or a first accepted slot above this node's
first, names packets the interlock dropped. Certificates go to the log and
to whoever waits for one.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

from . import packet as P
from .clock import Clock
from .log import Log
from .wire import Wire, WireClosed


@dataclass
class Sent:
    """A message handed to the dispatcher; `ids` fill in as packets leave."""
    payloads: list[bytes]
    key_commit: bytes
    reference: P.Id | None
    tag: object = None
    ids: list[P.Id] = field(default_factory=list)
    done: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def last(self) -> P.Id:
        return self.ids[-1]


@dataclass
class Stats:
    sent: int = 0
    received: int = 0
    dropped_order: int = 0
    dropped_malformed: int = 0
    lost: int = 0


class Dispatcher:
    def __init__(self, params: P.Params, wire: Wire, clock: Clock, log: Log, direction: int):
        self.params, self.wire, self.clock, self.log, self.direction = params, wire, clock, log, direction
        self.incoming: asyncio.Queue[P.Traffic] = asyncio.Queue()
        self.lost: asyncio.Queue[tuple[P.Id, object]] = asyncio.Queue()   # (id, tag) of packets the interlock dropped
        self.tags: dict[P.Id, object] = {}                                 # what each sent ID stands for
        self.stats = Stats()
        self._queue: asyncio.Queue[Sent | P.Nonce] = asyncio.Queue()
        self._bucket, self._slot = -1, 0
        self._in_bucket: dict[int, list[P.Id]] = defaultdict(list)         # bucket -> IDs sent into it
        self._last_rx: P.Id | None = None
        self._cert_waiters: list[tuple[Callable[[P.Certificate], bool], asyncio.Future]] = []
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        self._tasks = [asyncio.create_task(self._sender()), asyncio.create_task(self._receiver())]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        await self.wire.close()
        self.log.close()

    # --- sending -----------------------------------------------------------

    def send(self, payloads: list[bytes], key_commit: bytes = P.NO_KEY,
             reference: P.Id | None = None, tag: object = None) -> Sent:
        """Queue a message; returns at once. `tag` is what its packets stand
        for, found again through `tags` when a reply references one of them
        or through `lost` when the interlock dropped one."""
        if not payloads:
            raise ValueError("a message has at least one payload")
        sent = Sent(payloads, key_commit, reference, tag)
        self._queue.put_nowait(sent)
        return sent

    def nonce(self, nonce: bytes) -> None:
        self._queue.put_nowait(P.Nonce(P.Id(self.direction, 0, 0), nonce))

    def forget(self, sent: Sent) -> None:
        for id in sent.ids:
            self.tags.pop(id, None)

    async def _sender(self) -> None:
        await self.clock.ready()
        while True:
            item = await self._queue.get()
            if isinstance(item, P.Nonce):
                await self.wire.send(P.Nonce(P.Id(self.direction, max(self.clock.bucket(), self._bucket), 0), item.nonce).pack())
                continue
            prev = item.reference
            for payload in item.payloads:
                id = self._stamp(prev)
                await self.clock.wait_for(id.bucket)
                while self.clock.late(id.bucket):            # the estimate moved while waiting: stamp again
                    id = self._stamp(prev)
                    await self.clock.wait_for(id.bucket)
                raw = P.Traffic(id, prev if prev is not None else id, item.key_commit, payload).pack()
                item.ids.append(id)
                self.tags[id] = item.tag
                self.log.record(raw)
                self._in_bucket[id.bucket].append(id)
                await self.wire.send(raw)
                self.stats.sent += 1
                prev = id
            item.done.set()

    def _stamp(self, reference: P.Id | None) -> P.Id:
        bucket = max(self.clock.bucket(), self._bucket)
        if reference is not None and reference.dir != self.direction:
            bucket = max(bucket, reference.bucket + 1)
        if bucket != self._bucket:
            self._bucket, self._slot = bucket, 0
        if self._slot >= self.params.num_slot:
            self._bucket, self._slot = self._bucket + 1, 0
        id = P.Id(self.direction, self._bucket, self._slot)
        self._slot += 1
        return id

    # --- receiving ---------------------------------------------------------

    async def _receiver(self) -> None:
        while True:
            try:
                raw = await self.wire.recv()
            except WireClosed:
                return
            self._on_packet(raw)

    def _on_packet(self, raw: bytes) -> None:
        try:
            pkt = P.parse(raw)
        except P.FormatError:
            self.stats.dropped_malformed += 1
            return
        if isinstance(pkt, P.Sync):
            if pkt.dir == self.direction:
                self.clock.on_sync(pkt)
                self._on_sync(pkt)
            return
        if isinstance(pkt, P.Certificate):
            self.log.certificate(pkt)
            keep = []
            for pred, fut in self._cert_waiters:
                if not fut.done() and pred(pkt):
                    fut.set_result(pkt)
                elif not fut.done():
                    keep.append((pred, fut))
            self._cert_waiters = keep
            return
        if not isinstance(pkt, P.Traffic):
            self.stats.dropped_malformed += 1
            return
        if self._last_rx is not None and pkt.id.order <= self._last_rx.order:
            self.stats.dropped_order += 1
            return
        self._last_rx = pkt.id
        self.log.record(raw)
        self.stats.received += 1
        self.incoming.put_nowait(pkt)

    def _on_sync(self, sync: P.Sync) -> None:
        ids = self._in_bucket.pop(sync.bucket, None)
        if not ids:
            return
        lost = [id for id in ids if sync.first_slot == P.NO_SLOT or id.slot < sync.first_slot]
        for id in lost:
            self.stats.lost += 1
            self.lost.put_nowait((id, self.tags.get(id)))

    async def next_certificate(self, predicate: Callable[[P.Certificate], bool] = lambda c: True) -> P.Certificate:
        fut = asyncio.get_running_loop().create_future()
        self._cert_waiters.append((predicate, fut))
        return await fut
