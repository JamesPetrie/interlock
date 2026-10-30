"""The bucket clock a sender stamps BUCKET from.

The interlock accepts a packet only if its BUCKET equals the interlock's
current bucket when the header is checked, and the sync packets are what
the sender has to find that bucket with. A sync names the bucket that just
closed, so the sender always knows the current bucket number. It also
reports where in that bucket the first accepted packet was checked (DELTA),
and the sender knows when it sent that packet, which pins the interlock's
timeline to the local clock exactly, path delay included. `SyncClock` is
that lock, scheduling every packet a fixed fraction into its bucket.

`ManualClock` is for tests.
"""
from __future__ import annotations

import asyncio
import math
import time
from collections import deque

from .packet import NO_DELTA, NO_SLOT, Sync


class Clock:
    def bucket(self) -> int:
        """The earliest bucket a packet sent now can still be stamped with."""
        raise NotImplementedError

    async def wait_for(self, bucket: int) -> None:
        """Return when it is time to transmit a packet stamped `bucket`."""
        raise NotImplementedError

    async def ready(self) -> None:
        """Return once the clock can be read."""

    @property
    def synced(self) -> bool:
        return True

    def deadline(self, bucket: int) -> float | None:
        """Monotonic time to transmit a packet stamped `bucket`, or None if
        that is not known yet (wait for a sync)."""
        raise NotImplementedError

    def late(self, bucket: int) -> bool:
        """Whether a packet stamped `bucket` sent now would arrive after the bucket closed."""
        return False

    def open(self, bucket: int, at: float | None = None) -> bool:
        """Whether a packet sent now (or at monotonic time `at`) can still be
        stamped `bucket`: the bucket has not started, or it has and the packet
        would land early enough."""
        raise NotImplementedError

    def on_lost(self, n: int = 1) -> None:
        """Feedback: n of this sender's packets were dropped by the interlock."""

    def on_sent(self, id: tuple[int, int], t: float) -> None:
        """A packet stamped (bucket, slot) left at local time t."""

    def stats(self) -> dict:
        return {}

    def on_sync(self, sync: Sync) -> None:
        """Feedback from the interlock."""


class ManualClock(Clock):
    def __init__(self, bucket: int = 0):
        self._bucket = bucket
        self._event = asyncio.Event()

    def bucket(self) -> int:
        return self._bucket

    def advance(self, n: int = 1) -> None:
        self._bucket += n
        self._event.set()
        self._event = asyncio.Event()

    async def wait_for(self, bucket: int) -> None:
        while self._bucket < bucket:
            await self._event.wait()

    def deadline(self, bucket: int) -> float | None:
        return time.monotonic() if self._bucket >= bucket else None

    def transmit_time(self, bucket: int) -> float:
        """Tests: the instant of a bucket is now plus its distance from the current bucket, in ms."""
        return time.monotonic() + (bucket - self._bucket) * 1e-3

    def late(self, bucket: int) -> bool:
        return self._bucket > bucket

    def open(self, bucket: int, at: float | None = None) -> bool:
        return bucket >= self._bucket


class SyncClock(Clock):
    """Locks the local clock to the interlock's buckets through the syncs.

    A sync says bucket b has closed, which slot was accepted first in it and
    how far into the bucket (DELTA) that packet was checked. The sender knows
    at what local time it sent that very packet, so the three together pin
    the interlock's timeline to the local clock, path delay included: a
    packet sent at local time origin + b*L + x is checked x into bucket b.
    The schedule then puts every packet `target` into its bucket, counted
    from the fastest path seen among recent lock samples: delay only ever
    adds, so anchored to the fastest path a packet can never arrive before
    its bucket opens, and only the slow tail can miss. Until the first
    packet is accepted, the sync's
    arrival time gives a bound (bucket b+1 had started by then), used as a
    provisional origin that only ever moves earlier; a drop in that phase
    moves it earlier still, since the only way to miss is to be late.
    """

    def __init__(self, bucket_ms: float, target: float = 0.25, fill: float = 0.85,
                 delta_unit: float = 1e-6, samples: int = 32):
        """`target` where in the bucket a packet on the fastest path lands;
        `fill` how far into a bucket packets may still be sent for it;
        `delta_unit` the interlock's timer unit in seconds; `samples` how
        many lock samples the fastest-path window spans."""
        self.L = bucket_ms / 1000.0
        self.target = target
        self.fill = fill
        self.delta_unit = delta_unit
        self._bound: float | None = None                  # origin from sync arrivals, never later than the truth
        self._shift = 0.0                                 # bootstrap correction, earlier on a drop
        self._locks: deque[float] = deque(maxlen=samples)  # origin samples from our own packets' DELTA
        self._origin: float | None = None
        self._sent: dict[tuple[int, int], float] = {}     # (bucket, slot) -> local send time
        self._closed = -1
        self._event = asyncio.Event()
        self.syncs = 0
        self.lost = 0
        self.landed: deque[float] = deque(maxlen=200)

    @property
    def synced(self) -> bool:
        return self._origin is not None

    @property
    def locked(self) -> bool:
        return bool(self._locks)

    def transmit_time(self, bucket: int) -> float:
        return self._origin + (bucket + self.target) * self.L

    def deadline(self, bucket: int) -> float | None:
        return self.transmit_time(bucket) if self.synced else None

    def bucket(self) -> int:
        if self._origin is None:
            return 0
        behind = (time.monotonic() - self._origin) / self.L - self.target
        return max(self._closed + 1, math.ceil(behind))

    def interlock_bucket(self) -> int:
        """The bucket a packet sent now would be checked in."""
        return int((time.monotonic() - self._origin) / self.L)

    def late(self, bucket: int) -> bool:
        return self.synced and self.interlock_bucket() > bucket

    def open(self, bucket: int, at: float | None = None) -> bool:
        t = time.monotonic() if at is None else at
        return self.synced and (t - self._origin) / self.L - bucket < self.fill

    def on_sent(self, id: tuple[int, int], t: float) -> None:
        self._sent[id] = t
        if len(self._sent) > 4096:
            for k in sorted(self._sent)[:2048]:
                del self._sent[k]

    def on_sync(self, sync: Sync) -> None:
        self.syncs += 1
        now = time.monotonic()
        self._closed = max(self._closed, sync.bucket)
        bound = now - (sync.bucket + 1) * self.L
        if self._bound is None or bound < self._bound:
            self._bound = bound
        if sync.first_slot != NO_SLOT and sync.delta != NO_DELTA:
            delta = sync.delta * self.delta_unit
            self.landed.append(delta / self.L)
            t_sent = self._sent.get((sync.bucket, sync.first_slot))
            if t_sent is not None:
                self._locks.append(t_sent - delta - sync.bucket * self.L)
                self._origin = max(self._locks)                   # the fastest path: origin_true minus the least delay
        for k in [k for k in self._sent if k[0] <= sync.bucket]:
            del self._sent[k]
        if not self._locks:
            self._origin = self._bound + self._shift
        self._event.set()
        self._event = asyncio.Event()

    def on_lost(self, n: int = 1) -> None:
        self.lost += n
        if not self._locks and self._origin is not None:
            self._shift = max(-self.L, self._shift - 0.1 * self.L)
            self._origin = self._bound + self._shift

    async def ready(self) -> None:
        while self._origin is None:
            await self._event.wait()

    async def wait_for(self, bucket: int) -> None:
        await self.ready()
        while True:
            remaining = self.transmit_time(bucket) - time.monotonic()
            if remaining <= 0:
                return
            try:
                await asyncio.wait_for(self._event.wait(), remaining)   # a sync may move the estimate
            except asyncio.TimeoutError:
                return

    def stats(self) -> dict:
        recent = list(self.landed)
        locks = list(self._locks)
        return {"syncs": self.syncs, "lost": self.lost, "locked": self.locked, "target": self.target,
                "landed_mean": round(sum(recent) / len(recent), 3) if recent else None,
                "landed_max": round(max(recent), 3) if recent else None,
                "landed_min": round(min(recent), 3) if recent else None,
                "lock_spread_us": round((max(locks) - min(locks)) * 1e6) if locks else None}
