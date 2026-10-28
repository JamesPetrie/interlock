"""The bucket clock a sender stamps BUCKET from.

The interlock accepts a packet only if its BUCKET equals the interlock's
current bucket when the header is checked, and the sender tracks that bucket
from the sync packets: each sync names the bucket that just closed, so the
next one started when the sync was sent. `SyncClock` turns that into a
transmit schedule: a packet for bucket B leaves at the estimated start of B
plus a fraction `phase` of a bucket, aiming for the middle of the bucket so
that neither the link's delay nor its jitter pushes the packet into the
neighbouring bucket. The sync's DELTA, the interlock timer at the first
accepted header of a bucket, tells the sender where its packets actually
landed, and the phase follows it. Between syncs the clock runs on the local
monotonic clock at the agreed bucket length.

`ManualClock` is for tests.
"""
from __future__ import annotations

import asyncio
import math
import time

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

    def late(self, bucket: int) -> bool:
        """Whether a packet stamped `bucket` sent now would arrive after the bucket closed."""
        return False

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

    def late(self, bucket: int) -> bool:
        return self._bucket > bucket


class SyncClock(Clock):
    def __init__(self, bucket_ms: float, lead: int = 0, phase: float = 0.5,
                 delta_unit: float = 1e-6, gain: float = 0.25, drift: float = 0.02):
        """`lead` extra buckets of margin for a slow sender; `delta_unit` the
        interlock's timer unit in seconds; `gain` how fast the phase follows
        the DELTA feedback (0 = fixed phase); `drift` how fast the origin
        estimate may move later (it moves earlier at once)."""
        self.L = bucket_ms / 1000.0
        self.lead = lead
        self.phase = phase
        self.delta_unit = delta_unit
        self.gain = gain
        self.drift = drift
        self._origin: float | None = None     # local time at which the interlock's bucket 0 started
        self._closed = -1                     # bucket named by the latest sync
        self._event = asyncio.Event()
        self.syncs = 0

    @property
    def synced(self) -> bool:
        return self._origin is not None

    def transmit_time(self, bucket: int) -> float:
        """Local monotonic time at which a packet for `bucket` leaves."""
        return self._origin + (bucket + self.phase) * self.L

    def bucket(self) -> int:
        if self._origin is None:
            return 0
        behind = (time.monotonic() - self._origin) / self.L - self.phase
        return max(self._closed + 1, math.ceil(behind)) + self.lead

    def interlock_bucket(self) -> int:
        """The interlock's current bucket, as estimated now."""
        return int((time.monotonic() - self._origin) / self.L)

    def late(self, bucket: int) -> bool:
        return self.synced and self.interlock_bucket() > bucket

    def on_sync(self, sync: Sync) -> None:
        """A sync names the bucket that just closed, so bucket+1 started when
        the sync left the interlock: the earliest arrivals are the least
        delayed, so the origin follows a lower estimate at once and a higher
        one only slowly (clock drift between the two machines)."""
        self.syncs += 1
        candidate = time.monotonic() - (sync.bucket + 1) * self.L
        if self._origin is None or candidate < self._origin:
            self._origin = candidate
        else:
            self._origin += self.drift * (candidate - self._origin)
        self._closed = max(self._closed, sync.bucket)
        if self.gain and sync.first_slot != NO_SLOT and sync.delta != NO_DELTA:
            landed = sync.delta * self.delta_unit / self.L           # fraction of the bucket
            self.phase = min(0.9, max(0.1, self.phase + self.gain * (0.5 - landed)))
        self._event.set()
        self._event = asyncio.Event()

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
