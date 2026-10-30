"""The dispatcher: payloads in, packets out; packets in, payloads out. It
builds the buckets and keeps the log; getting each bucket onto the wire in
its millisecond is the release step's job (`--release`).

Building: a message's payloads become a chain of packets in this node's
direction. The builder places them into buckets ahead of time: the next
bucket the clock can still reach, later than a referenced packet of the
other direction, slots counting up within a bucket to the slot limit, each
later packet referencing its predecessor and the first what the caller
names (itself for a root, the request's last packet for a response, the
previous turn's last packet for a follow-up). A built bucket is final: its
packets are logged and tagged before they leave. Placement is policy and
lives only here; the response direction's fixed schedule goes here later.

Launching: `process` hands a built bucket to the pacer process, which
times it from user space on a core of its own; `kernel` stamps it with its
transmit instant and hands it to the kernel in one call, for an `fq` queue
on the interface to release from a kernel timer; `hardware` is `kernel`
with an `etf` queue offloaded to a NIC that releases at a launch time. The
in-loop sender (`loop`) times buckets from this event loop over a memory or
TCP wire; the tests run on it. Whoever releases assumes every packet can go
and drops what cannot, as the interlock would have; a dropped packet leaves
the log and is reported through `lost`.

Receiving: traffic packets must arrive in increasing ID order within the
direction, as the interlock guarantees; anything else is dropped and counted.
They are logged and handed over as they are, with IDs and references, since
only the holder of the keys can tell where a message ends. Syncs reveal
lost packets: a bucket this node built for whose sync reports nothing
accepted, or a first accepted slot above this node's first, names packets
that did not make it; they leave the log again, since no certificate
covers them, and go to `lost`. Losses behind the first accepted slot show
in the certificate audit; the node re-issues what gets no answer. A packet
whose send returned only after its bucket closed is a suspect: it stays in
the log, and the audit tries the certificate without it (`resolve`), so
that only random losses on the wire are left unexplained.
Suspects are a WORKAROUND for VM host stalls: on a VM the host can take the
vCPU away inside a send, at any point of the bucket (VCF: ~1 bucket in
100k). A bucket handed over in one call (TCP, sendmmsg) makes all its
packets suspects, though the first ones may have left on time; sent packet
by packet, only the one whose send spanned the close is. A stall in the
host after the send returned (its virtual NIC) shows as a random loss
instead, and under a release queue (fq, etf) the send returns before the
packet leaves, so a return in time proves nothing there. On bare metal a
send does not run past its bucket. Should exactness be needed, a sync
that names the accepted slots (a 64-bit map in its unused reference field)
would replace the suspects and the search.
Certificates go to the log, to the listeners (the frontend's audit) and to
whoever waits for one.
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket
import struct
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Callable

from . import packet as P
from .clock import Clock
from .log import Log
from .links import WIRE_BYTES
from .pacer import Framer, decode_ids, encode_bucket, run_pacer
from .wire import Wire, WireClosed

log = logging.getLogger(__name__)


@dataclass
class Sent:
    """A message handed to the dispatcher; `ids` fill in as buckets are built,
    `done` is set once the whole message is built and handed to the sender."""
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
    sent: int = 0                      # packets built and handed to the sender
    received: int = 0
    dropped_order: int = 0
    dropped_malformed: int = 0
    lost: int = 0                      # packets that did not make it, by the sender's or the syncs' account
    suspects: int = 0                  # packets whose send returned after their bucket closed (VM stall workaround)


@dataclass
class _Job:
    sent: Sent
    built: int = 0                     # payloads placed into buckets so far
    prev: P.Id | None = None           # the chain's last packet, for the next one's reference


@dataclass
class Bucket:
    number: int
    packets: list[tuple[P.Id, bytes, _Job]]


PACKET_US = 25.0          # what sending one packet costs a Python sender, until measured
LANDING = 0.25            # where the first packet of a bucket lands, until measured
FILL = 0.85               # the sender's fill limit, as the clocks have it
MARGIN = 0.1              # of a bucket, kept free at the end


def wire_us(link_mbps: float) -> float:
    """What one full packet takes on the wire."""
    return WIRE_BYTES * 8 / link_mbps


def slots_that_fit(params: P.Params, packet_us: float = PACKET_US, landing: float = LANDING,
                   link_mbps: float = 1000.0) -> int:
    """How many packets a sender can put into one bucket: from where its
    first packet lands to the fill limit less a margin, at the slower of
    its own cost per packet and the wire's."""
    window = (FILL - MARGIN - landing) * params.bucket_ms * 1000
    return max(1, min(params.num_slot, int(window / max(packet_us, wire_us(link_mbps)))))


class BucketBuilder:
    """Places packets into buckets: FIFO over messages, buckets in increasing
    order, `max_slots` per bucket. The slot cap follows what the sender
    measures (`adapt`) unless fixed."""

    def __init__(self, params: P.Params, direction: int, now_bucket: Callable[[], int], max_slots: int | None = None,
                 link_mbps: float = 1000.0):
        self.params, self.direction, self.now_bucket = params, direction, now_bucket
        self.fixed = max_slots is not None
        self.link_mbps = link_mbps
        self.max_slots = min(params.num_slot, max_slots or slots_that_fit(params, link_mbps=link_mbps))
        self._next = 0

    def adapt(self, landed_p90: float | None, packet_us: float | None) -> None:
        if not self.fixed and packet_us:
            self.max_slots = slots_that_fit(self.params, packet_us, landed_p90 if landed_p90 is not None else LANDING,
                                            self.link_mbps)

    @staticmethod
    def _needs(job: _Job, direction: int) -> int:
        """The earliest bucket the job's next packet may go into because of its reference."""
        ref = job.sent.reference
        if job.built == 0 and ref is not None and ref.dir != direction:
            return ref.bucket + 1
        return 0

    def build(self, jobs: deque[_Job]) -> list[Bucket]:
        out = []
        while jobs:
            b = max(self._next, self.now_bucket(), self._needs(jobs[0], self.direction))
            bucket = Bucket(b, [])
            while jobs and len(bucket.packets) < self.max_slots:
                job = jobs[0]
                if self._needs(job, self.direction) > b:
                    break                                          # needs a later bucket: close this one
                id = P.Id(self.direction, b, len(bucket.packets))
                ref = job.prev if job.prev is not None else (job.sent.reference if job.sent.reference is not None else id)
                raw = P.Traffic(id, ref, job.sent.key_commit, job.sent.payloads[job.built]).pack()
                bucket.packets.append((id, raw, job))
                job.sent.ids.append(id)
                job.prev, job.built = id, job.built + 1
                if job.built == len(job.sent.payloads):
                    jobs.popleft()
                    job.sent.done.set()
            if not bucket.packets:
                break
            out.append(bucket)
            self._next = b + 1
        return out


BOOTSTRAP_GRACE = 1e-3         # kernel/hardware release, before the clock is locked: a bucket's departure is at least this far ahead
                               # (the hand-over done well before it, so the frames leave at the stamp the clock records)


class Dispatcher:
    """Builds buckets and gets them onto the wire. `release` says who releases
    a bucket at its instant: `loop`, this event loop on its own timer, frame
    by frame (a TCP tunnel to a software interlock); `kernel`, the
    interface's `fq` queue from a kernel timer, which holds each frame to its
    stamp: the bucket is stamped with its instant and handed over in one
    call as soon as it is built, `ahead` buckets early; `hardware` is
    `kernel` with an `etf` queue offloaded to the NIC. `kernel` stamps in
    CLOCK_MONOTONIC, `fq`'s clock; `hardware` in CLOCK_TAI, `tai_offset`
    ahead of it. One stamp per bucket: one timer per bucket in the kernel,
    and the link's own rate spaces the frames; the queue must keep a
    bucket's order (`fq` does with the socket's frames in one flow, see
    `links.check_queue`). With `rate_limit`, every frame carries its own
    time, the instant plus its wire time at `link_mbps`: a bench with a
    link faster than the device behind it. For the pacer process,
    `rate_limit` means frame by frame at the wire time."""

    def __init__(self, params: P.Params, wire: Wire | None, clock: Clock | None, log: Log, direction: int,
                 max_slots: int | None = None, link_mbps: float = 1000.0, release: str = "loop", ahead: int = 2,
                 rate_limit: bool = False, tai_offset: float = 0.0):
        self.params, self.wire, self.clock, self.log, self.direction = params, wire, clock, log, direction
        self.release, self.ahead, self.rate_limit, self.tai_offset = release, ahead, rate_limit, tai_offset
        self.pace = WIRE_BYTES * 8 / link_mbps / 1e6 if link_mbps else 0.0
        self.event_listeners: list[Callable[[dict], None]] = []
        self.builder = BucketBuilder(params, direction, self._now_bucket, max_slots, link_mbps)
        self.incoming: asyncio.Queue[P.Traffic] = asyncio.Queue()
        self.lost: asyncio.Queue[tuple[P.Id, object]] = asyncio.Queue()   # (id, tag) of packets that did not make it
        self.tags: dict[P.Id, object] = {}                                 # what each built ID stands for
        self.suspects: set[P.Id] = set()                                   # sent as their bucket closed, not settled yet
        self.stats = Stats()
        self.certificate_listeners: list[Callable[[P.Certificate], None]] = []
        self._jobs: deque[_Job] = deque()
        self._buckets: deque[Bucket] = deque()
        self._wake = asyncio.Event()
        self._nonces: deque[bytes] = deque()
        self._in_bucket: dict[int, list[P.Id]] = defaultdict(list)         # bucket -> IDs sent into it
        self._last_rx: P.Id | None = None
        self._cert_waiters: list[tuple[Callable[[P.Certificate], bool], asyncio.Future]] = []
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        self._tasks = [asyncio.create_task(self._receiver())]
        if self.release == "loop":
            self._tasks.append(asyncio.create_task(self._sender()))

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

    # --- building -----------------------------------------------------------

    def send(self, payloads: list[bytes], key_commit: bytes = P.NO_KEY,
             reference: P.Id | None = None, tag: object = None) -> Sent:
        """Queue a message; its buckets are built at once. `tag` is what its
        packets stand for, found again through `tags` when a reply references
        one of them or through `lost` when one did not make it."""
        if not payloads:
            raise ValueError("a message has at least one payload")
        sent = Sent(payloads, key_commit, reference, tag)
        self._jobs.append(_Job(sent))
        self._build()
        return sent

    def _now_bucket(self) -> int:
        return self.clock.bucket() + (self.ahead if self.release != "loop" else 0)

    def _ready(self) -> bool:
        """Whether the current bucket is known, so that buckets can be built."""
        return self.clock.synced

    def _build(self) -> None:
        if not self._ready():
            return                                                  # built once the first sync tells the bucket
        for bucket in self.builder.build(self._jobs):
            for id, raw, job in bucket.packets:
                self.tags[id] = job.sent.tag
                self.log.record(raw, id)
                self._in_bucket[id.bucket].append(id)
            self.stats.sent += len(bucket.packets)
            self._dispatch(bucket)

    def _dispatch(self, bucket: Bucket) -> None:
        if self.release == "loop":
            self._buckets.append(bucket)
            self._wake.set()
        else:
            self._hand_over(bucket.number, [raw for _, raw, _ in bucket.packets])

    def _hand_over(self, number: int, raws: list[bytes]) -> None:
        """Kernel or hardware release: the bucket to the interface's queue,
        stamped with its instant (every frame with its own, with `rate_limit`);
        a bucket whose instant has passed is dropped, as the interlock would."""
        due = self.clock.transmit_time(number)
        now = time.monotonic()
        if not getattr(self.clock, "locked", True):                # bootstrap: the origin is a bound, deliberately early;
            due = max(due, now + BOOTSTRAP_GRACE)                  # send anyway, so the interlock's answer can lock the clock
        if now >= due:
            ids = [P.Id.unpack(int.from_bytes(r[8:16], "big")) for r in raws]
            self._event({"what": "late bucket", "bucket": number, "n": len(raws), "us": round((now - due) * 1e6)})
            self._drop(ids)
            return
        leave = [due + i * self.pace for i in range(len(raws))]           # when each frame is on the wire
        when = [t + self.tai_offset for t in leave] if self.rate_limit else due + self.tai_offset
        try:
            self.wire.send_bucket(raws, when)
        except OSError as e:
            self._event({"what": "refused", "bucket": number, "n": len(raws), "errno": e.errno})
            self._drop([P.Id.unpack(int.from_bytes(r[8:16], "big")) for r in raws])
            return
        for i, r in enumerate(raws):
            self.clock.on_sent((number, int.from_bytes(r[14:16], "big")), leave[i])
        if self.clock.late(number):                                # handed over only as the bucket closed
            self._suspect([P.Id.unpack(int.from_bytes(r[8:16], "big")) for r in raws])

    def _event(self, event: dict) -> None:
        for f in self.event_listeners:
            f(event)

    def nonce(self, nonce: bytes) -> None:
        if self.release == "loop":
            self._nonces.append(nonce)
            self._wake.set()
        else:
            b = self._now_bucket()
            self._hand_over(b, [P.Nonce(P.Id(self.direction, b, 0), nonce).pack()])

    def forget(self, sent: Sent) -> None:
        for id in sent.ids:
            self.tags.pop(id, None)

    def clock_stats(self) -> dict:
        return self.clock.stats()

    def builder_stats(self) -> dict:
        return {"next_bucket": self.builder._next, "jobs_waiting": len(self._jobs), "max_slots": self.builder.max_slots}

    # --- losses ----------------------------------------------------------------

    def _lost(self, ids: list[P.Id]) -> None:
        """Packets that did not make it: out of the log, and the node is told."""
        for id in ids:
            self.stats.lost += 1
            self.suspects.discard(id)
            self.log.remove(id)
            self.lost.put_nowait((id, self.tags.get(id)))

    def _suspect(self, ids: list[P.Id]) -> None:
        """Packets whose send returned only after their bucket closed: the
        interlock may or may not have taken them. They stay in the log; the
        sync settles the first of a bucket, the certificate audit the rest
        (`suspects_in`, `not_accepted`)."""
        for id in ids:
            if self.log.get(id) is not None:                       # not reported lost already
                self.suspects.add(id)
                self.stats.suspects += 1

    def suspects_in(self, cert: P.Certificate) -> list[P.Id]:
        """The unsettled suspects in the certificate's interval."""
        return sorted((id for id in self.suspects if cert.covers(id.bucket)), key=lambda i: i.order)

    def not_accepted(self, ids: list[P.Id]) -> None:
        """The certificate audit found these suspects missing: lost after all."""
        self._lost(ids)

    # --- the in-loop sender ------------------------------------------------------

    async def _sender(self) -> None:
        await self.clock.ready()
        while True:
            while not self._buckets and not self._nonces:
                await self._wake.wait()
                self._wake = asyncio.Event()
            while self._nonces:
                await self.wire.send(P.Nonce(P.Id(self.direction, self.clock.bucket(), 0), self._nonces.popleft()).pack())
            if not self._buckets:
                continue
            bucket = self._buckets.popleft()
            await self.clock.wait_for(bucket.number)
            gone, suspects = [], []
            for id, raw, job in bucket.packets:
                if gone or self.clock.late(bucket.number) or not self.clock.open(bucket.number):
                    gone.append(id)                                 # too late: dropped, as the interlock would have
                    continue
                self.clock.on_sent((id.bucket, id.slot), time.monotonic())
                await self.wire.send(raw)
                if self.clock.late(bucket.number):
                    suspects.append(id)
            if suspects:
                self._suspect(suspects)
            if gone:
                self._drop(gone)

    def _drop(self, ids: list[P.Id]) -> None:
        """The sender could not send these in their bucket. Only what no
        sync has reported lost already is counted (the sync of a bucket and
        the sender's verdict on it can come in either order)."""
        gone = set(ids)
        still = []
        for bucket in {id.bucket for id in ids}:
            built = self._in_bucket.get(bucket)
            if built is not None:
                still.extend(i for i in built if i in gone)
                self._in_bucket[bucket] = [i for i in built if i not in gone]
        if still:
            self._lost(still)

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
            for listener in self.certificate_listeners:
                listener(pkt)
            end = pkt.bkt_start + pkt.bkt_num
            self.suspects = {id for id in self.suspects if id.bucket >= end}   # the audit had its say on these
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
        """What the sync reveals: of the packets built for the closed bucket,
        those before the first accepted slot, or all if none was accepted,
        did not make it."""
        ids = self._in_bucket.pop(sync.bucket, None)
        if ids:
            lost = [id for id in ids if sync.first_slot == P.NO_SLOT or id.slot < sync.first_slot]
            if lost:
                if self.clock is not None:
                    self.clock.on_lost(len(lost))
                self._lost(lost)
        if sync.first_slot != P.NO_SLOT:
            self.suspects.discard(P.Id(self.direction, sync.bucket, sync.first_slot))   # accepted, the sync says
        if self._jobs:
            self._build()

    async def next_certificate(self, predicate: Callable[[P.Certificate], bool] = lambda c: True) -> P.Certificate:
        fut = asyncio.get_running_loop().create_future()
        self._cert_waiters.append((predicate, fut))
        return await fut


class ProcessDispatcher(Dispatcher):
    """A dispatcher whose sending runs in a pacer process (pacer.py): the
    pacer owns the wire and the clock; this side builds the buckets, keeps
    the log and the tags, and builds into the next bucket the pacer says it
    can still make (relayed with every sync), `ahead` buckets further to
    cover the relay and the build."""

    def __init__(self, params: P.Params, connect: tuple[str, int], log: Log, direction: int,
                 core: int | None = None, spin: float = 50e-6,
                 max_slots: int | None = None, ahead: int = 1, link_mbps: float = 1000.0, target: float = 0.25,
                 delta_unit: float = 1e-6, pace: bool = True, fill: float = 0.85):
        super().__init__(params, None, None, log, direction, max_slots, link_mbps)
        self._connect, self._core, self._spin = connect, core, spin
        self._target, self._delta_unit, self._pace, self._fill = target, delta_unit, pace, fill
        self._ahead = ahead
        self._current = 0
        self._current_at = 0.0                                     # when the pacer said so (monotonic, shared with the pacer)
        self._clock_stats: dict = {}
        self._proc = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def start(self) -> None:
        import multiprocessing as mp
        parent, child = socket.socketpair()
        for sk in (parent, child):
            sk.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 22)
            sk.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 22)
        host, port = self._connect
        self._proc = mp.get_context("spawn").Process(
            target=run_pacer, args=(child, host, port, self.params, self.direction,
                                    self._core, self._spin, self._target, self.builder.link_mbps, self._delta_unit, self._fill,
                                    self._pace),
            daemon=True, name=f"pacer-{self.direction:#06x}")
        self._proc.start()
        child.close()
        self._reader, self._writer = await asyncio.open_unix_connection(sock=parent)
        self._tasks = [asyncio.create_task(self._receiver())]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        if self._writer is not None:
            self._writer.close()
        if self._proc is not None:
            self._proc.terminate()
            self._proc.join(2)
        self.log.close()

    def clock_stats(self) -> dict:
        return dict(self._clock_stats)

    def builder_stats(self) -> dict:
        return {**super().builder_stats(), "current": self._current, "ahead": self._ahead,
                "receiver_alive": bool(self._tasks) and not self._tasks[0].done()}

    def _now_bucket(self) -> int:
        """The pacer's next reachable bucket, advanced with the clock since
        it last said so: a delayed relay (a burst of loss reports ahead of
        it) can then never make this side build buckets that are already
        past."""
        elapsed = int((time.monotonic() - self._current_at) * 1000.0 / self.params.bucket_ms) if self._current_at else 0
        return self._current + elapsed + self._ahead

    def _ready(self) -> bool:
        return self._current > 0

    def _dispatch(self, bucket: Bucket) -> None:
        self._writer.write(encode_bucket(bucket.number, [raw for _, raw, _ in bucket.packets]))

    def nonce(self, nonce: bytes) -> None:
        """The nonce packet goes out like any packet, in the next bucket the pacer can make."""
        b = self._now_bucket()
        self._writer.write(encode_bucket(b, [P.Nonce(P.Id(self.direction, b, 0), nonce).pack()]))

    async def _receiver(self) -> None:
        framer = Framer()
        while True:
            data = await self._reader.read(1 << 20)
            if not data:
                log.error("the pacer process is gone")
                return
            for i, (kind, body) in enumerate(framer.feed(data)):
                try:
                    self._on_frame(kind, body)
                except Exception:                               # one bad frame must not stop the dispatcher
                    log.exception("handling a %r frame from the pacer failed", kind)
                if i % 32 == 31:
                    await asyncio.sleep(0)                      # a flood of frames must not starve the builder

    def _on_frame(self, kind: bytes, body: bytes) -> None:
        body = bytes(body)
        if kind == b"D":
            self._drop(decode_ids(body))
        elif kind == b"S":
            self._suspect(decode_ids(body))
        elif kind == b"R":
            self._on_packet(body)
        elif kind == b"Y":
            next_bucket, when = struct.unpack_from("!Id", body, P.HEADER_SIZE)
            if next_bucket > self._current:
                self._current, self._current_at = next_bucket, when
            self._on_sync(P.parse(body[:P.HEADER_SIZE]))
        elif kind == b"C":
            st = json.loads(body)
            for event in st.pop("events", ()):
                for f in self.event_listeners:
                    f(event)
            self._clock_stats.update(st)
            self.builder.adapt(self._clock_stats.get("landed_p90"), self._clock_stats.get("packet_us"))
