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
        c = SyncClock(bucket_ms=20, target=0.5)
        assert not c.synced and c.bucket() == 0
        ready = asyncio.create_task(c.ready())
        await asyncio.sleep(0.01)
        assert not ready.done()
        c.on_sync(P.Sync(P.INBOUND, 10))                   # bucket 10 closed: 11 is current, half a bucket to its middle
        await asyncio.wait_for(ready, 1)
        assert c.bucket() == 11 and c.interlock_bucket() == 11 and not c.locked
        t0 = time.monotonic()
        await c.wait_for(12)                               # 1.5 buckets after the sync
        dt = time.monotonic() - t0
        assert 0.025 <= dt <= 0.06, dt
        assert c.bucket() in (12, 13) and c.interlock_bucket() in (12, 13)   # at 12's transmit time, 12 is just closing for us
        await c.wait_for(11)                               # already past: returns at once
        c.on_sync(P.Sync(P.INBOUND, 9))                    # an older sync never moves the clock back
        assert c.bucket() >= 12
    asyncio.run(main())


def test_sync_clock_locks_to_its_own_packets():
    L = 0.010
    c = SyncClock(bucket_ms=10, delta_unit=1e-6)
    c.on_sync(P.Sync(P.INBOUND, 4))                        # provisional origin from the arrival
    bound = c._origin
    t_sent = time.monotonic()
    c.on_sent((5, 2), t_sent)                              # our packet for bucket 5, slot 2, left now
    c.on_sync(P.Sync(P.INBOUND, 5, first_slot=2, delta=700))     # checked 0.7 ms into bucket 5
    assert c.locked and abs(c._origin - (t_sent - 0.0007 - 5 * L)) < 1e-9
    assert abs(c.transmit_time(6) - (t_sent - 0.0007 + L + 0.25 * L)) < 1e-9   # lands at the target next time
    assert c._origin < bound                                # the lock includes the path delay: earlier than the arrival bound
    assert c.stats()["lock_spread_us"] == 0 and c.stats()["landed_mean"] == 0.07
    c.on_sent((6, 0), t_sent + L)
    c.on_sync(P.Sync(P.INBOUND, 6, first_slot=0, delta=9000))    # one slow path: the fastest path keeps the schedule
    assert abs(c._origin - (t_sent - 0.0007 - 5 * L)) < 1e-9 and c.stats()["lock_spread_us"] == 8300
    c.on_sync(P.Sync(P.INBOUND, 7, first_slot=1, delta=100))     # a slot we did not record: ignored for the lock
    assert len(c._locks) == 2
    c.on_lost(2)                                           # locked: a drop is jitter, the schedule stays
    assert c.lost == 2 and abs(c._origin - (t_sent - 0.0007 - 5 * L)) < 1e-9
    c.on_sent((8, 0), t_sent + 3 * L)
    c.on_sync(P.Sync(P.INBOUND, 8, first_slot=0, delta=200))     # a faster path than before: the schedule moves later
    assert abs(c._origin - (t_sent - 0.0002 - 5 * L)) < 1e-9


def test_sync_clock_bootstrap_only_moves_earlier():
    L = 0.010
    c = SyncClock(bucket_ms=10)
    c.on_sync(P.Sync(P.INBOUND, 0))
    origin = c._origin
    time.sleep(0.004)
    c.on_sync(P.Sync(P.INBOUND, 0))                        # the same bucket seen 4 ms later: a delayed sync changes nothing
    assert c._origin == origin
    c.on_lost()                                            # not locked yet: the only way to miss is being late
    assert abs(c._origin - (origin - 0.1 * L)) < 1e-9
    time.sleep(0.003)
    c.on_sync(P.Sync(P.INBOUND, 3))                        # 7 ms early against the estimate: the bound moves at once
    assert c._origin < origin - 0.005

