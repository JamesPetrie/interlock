import asyncio
import time

from harness import MAC, run

from ilstack import packet as P
from ilstack.dispatcher import ProcessDispatcher
from ilstack.interlock import RealTicker, SoftwareInterlock
from ilstack.log import MemoryLog
from ilstack.links import PacketFramer, queue_problem
from ilstack.pacer import Framer, decode_bucket, decode_ids, encode_bucket, encode_ids, fitting, frame, id_of
from ilstack.wire import TcpServerWire

KC = bytes(range(32))


def test_framing_roundtrips():
    ids = [P.Id(P.INBOUND, 1, 0), P.Id(P.INBOUND, 1, 1)]
    raws = [P.Traffic(ids[0], ids[0], KC, b"abcd").pack(), P.Traffic(ids[1], ids[0], KC, b"").pack()]
    raw = encode_bucket(7, raws)
    f = Framer()
    assert f.feed(raw[:3]) == []
    ((kind, body),) = f.feed(raw[3:])
    assert kind == b"B" and decode_bucket(body) == (7, raws)
    kind, body = f.feed(encode_ids(b"L", ids))[0]
    assert kind == b"L" and decode_ids(body) == ids
    two = f.feed(frame(b"R", raws[0]) + frame(b"C", b"{}"))
    assert [k for k, _ in two] == [b"R", b"C"]
    assert [id_of(r) for r in raws] == ids
    pf = PacketFramer()
    assert pf.feed(raws[0][:10]) == [] and pf.feed(raws[0][10:] + raws[1] + bytes(64)[:7]) == raws
    cert = P.Certificate(6, 0, 0, 10, bytes(16), bytes(32), bytes(32), bytes(32)).packet()
    assert pf.feed(cert[7:]) == [cert]


def test_queue_check_from_tc():
    """Kernel release: an fq root queue with a bucket's quantum, room for the parked buckets, one flow, no maxrate;
    hardware: an offloaded etf. Otherwise the tc command that fixes it."""
    fix = ("tc qdisc replace dev enp1s0np0 root fq quantum 100000 initial_quantum 100000 flow_limit 2000 limit 10000 "
           "orphan_mask 0 timer_slack 1us")
    good = ("qdisc fq 8001: root refcnt 2 limit 10000p flow_limit 2000p buckets 1024 orphan_mask 0 quantum 100000b "
            "initial_quantum 100000b low_rate_threshold 550Kbit refill_delay 40ms timer_slack 1us horizon 10s horizon_drop\n")
    assert queue_problem(good, "enp1s0np0", "kernel", 64, 10) is None
    newer = good.replace("orphan_mask 0 ", "orphan_mask 0 bands 3 priomap 1 2 2 2 1 2 0 0 1 1 1 1 1 1 1 1 weights 589824 196608 65536 ")
    assert queue_problem(newer, "enp1s0np0", "kernel", 64, 10) is None                    # iproute2 >= 6.7 prints the bands
    assert queue_problem(good.replace("root", "dev enp1s0np0 root"), "enp1s0np0", "kernel", 64, 10) is None
    sizes = good.replace("quantum 100000b", "quantum 128Kb").replace("initial_quantum 128Kb", "initial_quantum 1Mb")
    assert queue_problem(sizes, "enp1s0np0", "kernel", 64, 10) is None                    # tc's 1024-based units
    hashed = queue_problem(good.replace("orphan_mask 0", "orphan_mask 1023"), "enp1s0np0", "kernel", 64, 10)
    assert "orphan_mask 1023" in hashed and hashed.endswith("\n  " + fix)
    default = ("qdisc fq 8002: root refcnt 2 limit 10000p flow_limit 100p buckets 1024 orphan_mask 1023 quantum 3028b "
               "initial_quantum 15140b low_rate_threshold 550Kbit refill_delay 40ms timer_slack 10us horizon 10s horizon_drop\n")
    p = queue_problem(default, "enp1s0np0", "kernel", 64, 10)
    assert "quantum 3028 < 96896" in p and "initial_quantum 15140 < 96896" in p and "flow_limit 100 < 704" in p
    assert "limit 10000" not in p.split("\n")[0]
    assert "maxrate" in queue_problem(good.replace("low_rate", "maxrate 1Gbit low_rate"), "enp1s0np0", "kernel", 64, 10)
    mq = ("qdisc mq 0: root \nqdisc fq_codel 0: parent :2 limit 10240p flows 1024 quantum 1514 target 5ms interval 100ms "
          "memory_limit 32Mb ecn drop_batch 64 \nqdisc fq_codel 0: parent :1 limit 10240p flows 1024 quantum 1514 target 5ms "
          "interval 100ms memory_limit 32Mb ecn drop_batch 64 \n")
    assert queue_problem(mq, "enp1s0np0", "kernel", 64, 10) == f"kernel release on enp1s0np0: the root queue is mq, not fq. Set it with\n  {fix}"
    assert "missing" in queue_problem("", "enp1s0np0", "kernel", 64, 10)
    big = queue_problem(mq, "x", "kernel", 256, 10)                                    # the command sized to the need
    assert "quantum 387584 initial_quantum 387584 flow_limit 2816 limit 10000" in big
    etf = "qdisc etf 8031: root refcnt 761 clockid TAI delta 300000 offload off deadline_mode off skip_sock_check off\n"
    assert "not fq" in queue_problem(etf, "enp1s0np0", "kernel", 64, 10)
    assert queue_problem(etf.replace("offload off", "offload on"), "enp1s0np0", "hardware", 64, 10) is None
    for q in (etf, mq, good):
        assert queue_problem(q, "enp1s0np0", "hardware", 64, 10).endswith(
            "\n  tc qdisc replace dev enp1s0np0 root etf clockid CLOCK_TAI delta 300000 offload")
    assert queue_problem(mq, "enp1s0np0", "process", 64, 10) is None


def test_bucket_builder():
    from collections import deque
    from ilstack.dispatcher import BucketBuilder, Sent, _Job
    now = [4]
    b = BucketBuilder(P.Params(num_slot=4), P.OUTBOUND, lambda: now[0], max_slots=3)
    jobs = deque([_Job(Sent([b"a", b"b", b"c", b"d"], P.NO_KEY, P.Id(P.INBOUND, 4, 1), tag="r1")),
                  _Job(Sent([b"e"], P.NO_KEY, None, tag="r2"))])
    out = b.build(jobs)
    assert [(x.number, [p[0].slot for p in x.packets]) for x in out] == [(5, [0, 1, 2]), (6, [0, 1])]   # later than the reference; 3 slots
    ids = [p[0] for x in out for p in x.packets]
    refs = [P.parse(p[1]).reference for x in out for p in x.packets]
    assert refs == [P.Id(P.INBOUND, 4, 1), ids[0], ids[1], ids[2], ids[4]]                            # chain, then a root
    assert not jobs
    jobs.append(_Job(Sent([b"f"], P.NO_KEY, P.Id(P.INBOUND, 20, 0))))
    jobs.append(_Job(Sent([b"g"], P.NO_KEY, None)))
    out = b.build(jobs)
    assert [(x.number, len(x.packets)) for x in out] == [(21, 2)]                                       # both wait for the later bucket
    now[0] = 30
    assert b.build(deque([_Job(Sent([b"h"], P.NO_KEY, None))]))[0].number == 30
    from ilstack.dispatcher import slots_that_fit, wire_us
    assert round(wire_us(1000), 2) == 12.3
    assert slots_that_fit(P.Params(bucket_ms=1)) == 20 and slots_that_fit(P.Params(bucket_ms=10)) == 64
    assert slots_that_fit(P.Params(bucket_ms=1), packet_us=30, landing=0.6) == 5
    assert slots_that_fit(P.Params(bucket_ms=1), packet_us=5, landing=0.42) == 26          # the wire, not the sender, bounds it
    assert slots_that_fit(P.Params(bucket_ms=1), packet_us=5, landing=0.42, link_mbps=10000) == 64
    assert slots_that_fit(P.Params(bucket_ms=0.01)) == 1
    b.adapt(0.5, 50.0)
    assert b.max_slots == 3                                                                          # capped by the builder's own limit
    fixed = BucketBuilder(P.Params(), P.INBOUND, lambda: 0, max_slots=10)
    fixed.adapt(0.9, 100.0)
    assert fixed.max_slots == 10


class OpenUntil:
    """A clock whose bucket takes packets sent before `end`."""
    def __init__(self, end):
        self.end = end

    def open(self, bucket, at=None):
        return at < self.end


def test_a_burst_sends_only_what_lands_inside_the_bucket():
    wire = 12.3e-6                                                  # a full packet at 1 Gb/s
    assert fitting(OpenUntil(1.0), 0, 0.5, 40, wire) == 40          # room for all
    assert fitting(OpenUntil(1.0), 0, 1.0 - 10.5 * wire, 40, wire) == 11   # a late start: the first 11
    assert fitting(OpenUntil(1.0), 0, 1.0, 40, wire) == 0           # nothing left
    assert fitting(OpenUntil(1.0), 0, 0.99, 40, 0.0) == 40          # no wire time known: all or nothing
    assert fitting(OpenUntil(1.0), 0, 0.99, 0, wire) == 0


def test_pacer_processes_end_to_end():
    async def main():
        params = P.Params(num_slot=8, bkt_num=20, bucket_ms=10)
        fe_port = await TcpServerWire.listen("127.0.0.1", 0)
        co_port = await TcpServerWire.listen("127.0.0.1", 0)
        il = SoftwareInterlock(params, fe_port, co_port, MAC, RealTicker(params.bucket_ms))
        await il.start()
        fe = ProcessDispatcher(params, ("127.0.0.1", fe_port.port), MemoryLog(), P.INBOUND)
        co = ProcessDispatcher(params, ("127.0.0.1", co_port.port), MemoryLog(), P.OUTBOUND)
        await fe.start()
        await co.start()
        try:
            t0 = time.monotonic()
            for i in range(5):
                sent = fe.send([bytes([i]) * 4, bytes([i]) * 8], KC, tag=i)
                a = await asyncio.wait_for(co.incoming.get(), 10)
                b = await asyncio.wait_for(co.incoming.get(), 10)
                await asyncio.wait_for(sent.done.wait(), 1)
                assert (a.id, b.id) == tuple(sent.ids) and a.reference == a.id and b.reference == a.id
                assert a.key_commit == KC and b.payload == bytes([i]) * 8
                reply = co.send([bytes([i]) * 4], reference=sent.last, tag=i)
                r = await asyncio.wait_for(fe.incoming.get(), 10)
                await asyncio.wait_for(reply.done.wait(), 1)
                assert r.id == reply.ids[0] and r.reference == sent.last and fe.tags[r.reference] == i
                assert r.id.bucket > sent.last.bucket
            assert time.monotonic() - t0 < 5
            big = fe.send([bytes(1436)] * 40, KC, tag="big")             # a message of many packets fills buckets
            got = [await asyncio.wait_for(co.incoming.get(), 10) for _ in range(40)]
            await asyncio.wait_for(big.done.wait(), 1)
            assert [p.id for p in got] == big.ids and len({i.bucket for i in big.ids}) <= 6, big.ids   # 8 slots per bucket
            assert max(i.slot for i in big.ids) == 7                                                 # buckets were filled
            nonce = b"n" * 16
            waiter = asyncio.create_task(fe.next_certificate(lambda c: c.nonce == nonce))
            fe.nonce(nonce)
            cert = await asyncio.wait_for(waiter, 5)
            assert cert.verify(MAC) and fe.log.certificates[-1] == cert
            await asyncio.sleep(0.1)
            assert fe.stats.lost == 0 and co.stats.lost == 0 and fe.stats.sent == 50 and co.stats.sent == 5
            assert all(fe.log.get(i) is not None for i in big.ids)
            assert fe.log.get(fe.tags and list(fe.tags)[0]) is not None
            await asyncio.sleep(1.1)
            assert "syncs" in fe.clock_stats() and fe.clock_stats()["syncs"] > 10
        finally:
            await fe.stop()
            await co.stop()
            await il.stop()
    run(main(), timeout=60)
