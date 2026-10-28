import asyncio
import time

from ilstack import packet as P
from ilstack.clock import ManualClock, SyncClock


def test_manual_clock():
    async def main():
        c = ManualClock(3)
        assert c.bucket() == 3
        waiter = asyncio.create_task(c.wait_for(5))
        await asyncio.sleep(0)
        assert not waiter.done()
        c.advance(2)
        await asyncio.wait_for(waiter, 1)
    asyncio.run(main())


def test_sync_clock_schedule():
    async def main():
        c = SyncClock(bucket_ms=20, gain=0)
        assert not c.synced and c.bucket() == 0
        ready = asyncio.create_task(c.ready())
        await asyncio.sleep(0.01)
        assert not ready.done()
        c.on_sync(P.Sync(P.INBOUND, 10))                   # bucket 10 closed: 11 is current, half a bucket to its middle
        await asyncio.wait_for(ready, 1)
        assert c.bucket() == 11 and c.interlock_bucket() == 11
        t0 = time.monotonic()
        await c.wait_for(12)                               # 1.5 buckets after the sync
        dt = time.monotonic() - t0
        assert 0.025 <= dt <= 0.06, dt
        assert c.bucket() in (12, 13) and c.interlock_bucket() == 12   # at 12's transmit time, 12 is just closing for us
        await c.wait_for(11)                               # already past: returns at once
        c.on_sync(P.Sync(P.INBOUND, 9))                    # an older sync never moves the clock back
        assert c.bucket() >= 12
        assert SyncClock(bucket_ms=20, lead=2, gain=0).lead == 2
    asyncio.run(main())


def test_sync_clock_follows_delta():
    c = SyncClock(bucket_ms=10, delta_unit=1e-6)
    c.on_sync(P.Sync(P.INBOUND, 1))
    assert c.phase == 0.5
    c.on_sync(P.Sync(P.INBOUND, 2, first_slot=0, delta=9000))     # landed at 0.9 of the bucket: too late
    assert c.phase < 0.5
    c.on_sync(P.Sync(P.INBOUND, 3, first_slot=0, delta=500))      # landed at 0.05: too early
    assert c.phase > 0.4
    c.on_sync(P.Sync(P.INBOUND, 4))                                # nothing accepted: no information
    phase = c.phase
    for _ in range(50):
        c.on_sync(P.Sync(P.INBOUND, 5, 0, 10000))
    assert c.phase == 0.1 and c.syncs == 54                        # clipped
    assert phase > 0.1
