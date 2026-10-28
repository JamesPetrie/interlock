import asyncio

from harness import make_link, run, settle

from ilstack import packet as P

KC = bytes(range(32))


async def recv(dispatcher, n):
    return [await asyncio.wait_for(dispatcher.incoming.get(), 2) for _ in range(n)]


def test_request_and_response_by_hand():
    async def main():
        link = await make_link(P.Params(num_slot=8, bkt_num=4))
        sent = link.fe.send([b"aaaa", b"bbbb"], KC, tag="r1")
        await settle()
        assert sent.done.is_set() and sent.ids == [P.Id(P.INBOUND, 0, 0), P.Id(P.INBOUND, 0, 1)]
        a, b = await recv(link.co, 2)
        assert (a.id, a.reference, a.key_commit, a.payload) == (sent.ids[0], sent.ids[0], KC, b"aaaa")
        assert (b.id, b.reference, b.payload) == (sent.ids[1], sent.ids[0], b"bbbb")
        reply = link.co.send([b"cccc"], reference=sent.last, tag="a1")
        await settle()
        assert not reply.done.is_set()                              # a response needs a later bucket than its request
        await link.advance()
        (r,) = await recv(link.fe, 1)
        assert r.id == P.Id(P.OUTBOUND, 1, 0) and r.reference == sent.last and r.key_commit == P.NO_KEY
        assert link.fe.tags[r.reference] == "r1" and link.co.tags[r.id] == "a1"
        await link.advance(2)                                       # both buckets' syncs are in: nothing lost
        assert link.fe.lost.empty() and link.co.lost.empty()
        assert link.fe.stats.sent == 2 and link.fe.stats.received == 1 and link.co.stats.received == 2
        assert link.il.counters[P.INBOUND].drops == 0 and link.il.counters[P.OUTBOUND].drops == 0
        assert link.fe.log.get(sent.ids[1]) is not None and link.co.log.get(r.id) is not None
        link.fe.forget(sent)
        assert sent.ids[0] not in link.fe.tags
        await link.stop()
    run(main())


def test_slots_roll_into_the_next_bucket():
    async def main():
        link = await make_link(P.Params(num_slot=2, bkt_num=100))
        sent = link.fe.send([bytes(4)] * 5, KC)
        await settle()
        assert sent.ids == [P.Id(P.INBOUND, 0, 0), P.Id(P.INBOUND, 0, 1)] and not sent.done.is_set()
        await link.advance()
        await link.advance()
        assert sent.done.is_set()
        assert [i.order for i in sent.ids] == [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0)]
        got = await recv(link.co, 5)
        assert [p.id for p in got] == sent.ids and [p.reference for p in got] == [sent.ids[0]] + sent.ids[:4]
        later = link.fe.send([bytes(4)], KC)
        await settle()
        assert later.ids == [P.Id(P.INBOUND, 2, 1)]                 # continues in the current bucket
        await link.stop()
    run(main())


def test_lost_packets_are_reported_from_the_sync():
    async def main():
        link = await make_link(P.Params(num_slot=8, bkt_num=100))
        link.fe_wire.drop = lambda raw: raw[14:16] == b"\x00\x00"      # lose slot 0 of every bucket
        first = link.fe.send([b"aaaa", b"bbbb"], KC, tag="first")
        await settle()
        assert len(await recv(link.co, 1)) == 1                     # slot 1 still gets through
        await link.advance()
        assert await asyncio.wait_for(link.fe.lost.get(), 1) == (first.ids[0], "first")
        assert link.fe.lost.empty() and link.fe.stats.lost == 1
        link.fe_wire.drop = lambda raw: True                        # now everything is lost
        gone = link.fe.send([b"cccc", b"dddd"], KC, tag="gone")
        await settle()
        await link.advance()
        assert [await link.fe.lost.get() for _ in range(2)] == [(gone.ids[0], "gone"), (gone.ids[1], "gone")]
        link.fe_wire.drop = None
        fine = link.fe.send([b"eeee"], KC, tag="fine")
        await settle()
        await link.advance()
        assert link.fe.lost.empty() and fine.done.is_set()
        await link.stop()
    run(main())


def test_receive_order_and_malformed():
    async def main():
        link = await make_link(P.Params(num_slot=8, bkt_num=100))
        d = link.co
        a = P.Traffic(P.Id(P.INBOUND, 3, 1), P.Id(P.INBOUND, 3, 1), KC, b"aaaa")
        d._on_packet(a.pack())
        d._on_packet(P.Traffic(P.Id(P.INBOUND, 3, 0), P.Id(P.INBOUND, 3, 0), KC, b"").pack())   # earlier than the last
        d._on_packet(a.pack())                                                                   # repeated
        d._on_packet(a.pack()[:-1])
        d._on_packet(P.Sync(P.INBOUND, 3).pack())                                                # not our direction: ignored
        assert d.stats.received == 1 and d.stats.dropped_order == 2 and d.stats.dropped_malformed == 1
        assert (await recv(d, 1))[0].id == a.id
        await link.stop()
    run(main())


def test_nonce_and_certificate():
    async def main():
        link = await make_link(P.Params(num_slot=8, bkt_num=3))
        nonce = b"n" * 16
        link.fe.nonce(nonce)
        await settle()
        assert link.il.nonce == nonce
        waiting = asyncio.create_task(link.fe.next_certificate(lambda c: c.nonce == nonce))
        await link.advance(3)
        cert = await asyncio.wait_for(waiting, 1)
        assert cert.bkt_start == 0 and cert.verify(b"m" * 32) and link.fe.log.certificates == [cert]
        assert link.co.log.certificates == []
        await link.stop()
    run(main())


def test_real_time_round_trips():
    async def main():
        link = await make_link(P.Params(num_slot=8, bkt_num=20, bucket_ms=20), manual=False)
        for i in range(5):
            req = link.fe.send([bytes([i]) * 4] * 2, KC, tag=i)
            (a, b) = await recv(link.co, 2)
            assert a.payload == b.payload == bytes([i]) * 4
            link.co.send([bytes([i]) * 8], reference=b.id, tag=i)
            (r,) = await recv(link.fe, 1)
            assert r.reference == req.last and link.fe.tags[r.reference] == i
        await asyncio.sleep(0.1)
        assert link.fe.stats.lost == 0 and link.co.stats.lost == 0
        assert link.il.counters[P.INBOUND].accepted == 10 and link.il.counters[P.OUTBOUND].accepted == 5
        assert link.il.counters[P.INBOUND].drops == 0 and link.il.counters[P.OUTBOUND].drops == 0
        assert link.clocks[0].syncs > 5
        await link.stop()
    run(main())
