import asyncio
import multiprocessing as mp
import socket
import time

from ilstack import packet as P
from ilstack.timing import Stub, run_send, run_stub


def test_stub_judges_and_syncs():
    t0 = 100.0
    s = Stub(P.Params(bucket_ms=1), P.INBOUND, t0)
    kc = bytes(32)

    def pkt(b, slot):
        return P.Traffic(P.Id(P.INBOUND, b, slot), P.Id(P.INBOUND, b, slot), kc, bytes(4)).pack()
    s.feed(pkt(3, 0), t0 + 0.0031)                    # bucket 3, 100 us in
    s.feed(pkt(3, 1), t0 + 0.0035)
    s.feed(pkt(2, 0), t0 + 0.0036)                    # late by one
    s.feed(pkt(5, 0), t0 + 0.0037)                    # early
    s.feed(P.Sync(P.INBOUND, 1).pack(), t0 + 0.0038)  # not traffic
    assert (s.accepted, s.late, s.early, s.other) == (2, 1, 1, 1) and s.lateness == {1: 1}
    assert P.parse(s.close(2)) == P.Sync(P.INBOUND, 2)
    sync = P.parse(s.close(3))
    assert sync.first_slot == 0 and 90 <= sync.delta <= 110
    assert s.counts == [2] and 390 <= s.spans[0] <= 410 and "late 1" in s.report()


def _stub(port):
    run_stub("127.0.0.1", port, P.INBOUND, 10.0, None, None, report_every=1000)


def test_send_against_stub():
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    proc = mp.get_context("spawn").Process(target=_stub, args=(port,), daemon=True)
    proc.start()
    try:
        time.sleep(0.5)
        params = P.Params(bucket_ms=10, bkt_num=10)
        for pacer, pace in ((True, True), (True, False), (False, True)):
            stats = asyncio.run(asyncio.wait_for(run_send("127.0.0.1", port, params, 8, 20, release="process", rate_limit=pace), 60))
            assert stats["dispatcher"]["sent"] == 160 and stats["dispatcher"]["lost"] == 0, stats
            assert stats["clock"]["landed_max"] is not None and stats["clock"]["landed_max"] < 0.9, stats
            if pacer:
                assert stats["clock"]["pace_us"] == (12.3 if pace else 0.0), stats["clock"]
    finally:
        proc.terminate()
        proc.join(2)
