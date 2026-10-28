import asyncio
import hashlib

from ilstack import packet as P
from ilstack.interlock import SoftwareInterlock, Ticker
from ilstack.wire import MemoryWire

KC = bytes(range(32))
MAC = b"m" * 32


class ManualTicker(Ticker):
    def __init__(self):
        self._bucket = 0
        self._delta = 0
        self._event = asyncio.Event()

    def bucket(self):
        return self._bucket

    def delta(self, bucket):
        return self._delta

    async def wait_for(self, bucket):
        while self._bucket < bucket:
            await self._event.wait()

    def advance(self):
        self._bucket += 1
        self._event.set()
        self._event = asyncio.Event()


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


def run(coro):
    asyncio.run(asyncio.wait_for(coro, 10))


def req(bucket, slot, ref=None, payload=b"abcd", kc=KC):
    id = P.Id(P.INBOUND, bucket, slot)
    return P.Traffic(id, ref or id, kc, payload)


def resp(bucket, slot, ref, payload=b"abcd"):
    return P.Traffic(P.Id(P.OUTBOUND, bucket, slot), ref, payload=payload)


def test_rules_and_forwarding():
    async def main():
        fe, il_fe = MemoryWire.pair()
        co, il_co = MemoryWire.pair()
        tk = ManualTicker()
        il = SoftwareInterlock(P.Params(num_slot=4, bkt_num=5), il_fe, il_co, MAC, tk)
        await il.start()
        tk.advance(); tk.advance(); await settle()                 # now bucket 2
        for _ in range(2):                                         # the syncs for buckets 0 and 1
            assert isinstance(P.parse(await fe.recv()), P.Sync) and isinstance(P.parse(await co.recv()), P.Sync)
        ok = req(2, 1)
        await fe.send(ok.pack()); await settle()
        assert await co.recv() == ok.pack()                        # accepted and forwarded at once
        drops = [
            ("header", req(2, 2).pack()[:-1]),                     # PLD_LEN disagrees with the bytes
            ("reserved", req(2, 2).pack()[:24] + b"\1" + req(2, 2).pack()[25:]),
            ("type", P.Sync(P.INBOUND, 1).pack()),                 # a sender may not send control packets
            ("size", req(2, 2, payload=bytes(1440)).pack()),
            ("dir", resp(2, 2, ok.id).pack()),                     # a response on the inbound port
            ("bucket", req(1, 2).pack()),                          # late
            ("bucket", req(3, 2).pack()),                          # early
            ("slot", req(2, 4).pack()),                            # NUM_SLOT is 4
            ("order", req(2, 1).pack()),                           # not after slot 1
            ("order", req(2, 0).pack()),
            ("reference", req(2, 2, ref=P.Id(P.INBOUND, 2, 3)).pack()),     # later than itself
            ("reference", req(2, 2, ref=P.Id(P.OUTBOUND, 2, 0)).pack()),    # other direction, same bucket
            ("units", req(2, 2, payload=b"abcde").pack()),
        ]
        for rule, raw in drops:
            before = il.counters[P.INBOUND].dropped[rule]
            await fe.send(raw); await settle()
            assert il.counters[P.INBOUND].dropped[rule] == before + 1, rule
        assert co._in.empty() and il.counters[P.INBOUND].accepted == 1
        # the response side: a response must sit in a later bucket than the request it references
        await co.send(resp(2, 0, ok.id).pack()); await settle()
        assert il.counters[P.OUTBOUND].dropped["reference"] == 1
        tk.advance(); await settle()                               # bucket 3
        assert P.parse(await fe.recv()).bucket == 2 and P.parse(await co.recv()).bucket == 2
        r = resp(3, 0, ok.id)
        await co.send(r.pack()); await settle()
        assert await fe.recv() == r.pack()
        bad = resp(3, 1, P.Id(P.INBOUND, 2, 1), payload=b"")       # fine: empty payload is a whole number of units
        await co.send(bad.pack()); await settle()
        assert await fe.recv() == bad.pack()
        await co.send(req(3, 3).pack()); await settle()            # a request on the outbound port
        assert il.counters[P.OUTBOUND].dropped["dir"] == 1 and il.counters[P.OUTBOUND].accepted == 2
        # a nonce is latched and never forwarded
        await fe.send(P.Nonce(P.Id(P.INBOUND, 3, 0), b"n" * 16).pack()); await settle()
        assert il.nonce == b"n" * 16 and il.counters[P.INBOUND].nonces == 1 and co._in.empty()
        await co.send(P.Nonce(P.Id(P.INBOUND, 3, 0), b"x" * 16).pack()); await settle()
        assert il.nonce == b"n" * 16 and il.counters[P.OUTBOUND].dropped["type"] == 1
        await il.stop()
    run(main())


def test_syncs_and_certificates():
    async def main():
        fe, il_fe = MemoryWire.pair()
        co, il_co = MemoryWire.pair()
        tk = ManualTicker()
        il = SoftwareInterlock(P.Params(num_slot=4, bkt_num=3, device=7), il_fe, il_co, MAC, tk)
        await il.start()
        tk._delta = 123
        a = req(0, 2)
        await fe.send(a.pack()); await settle()
        await co.recv()
        tk.advance(); await settle()                               # bucket 0 closes
        assert P.parse(await fe.recv()) == P.Sync(P.INBOUND, 0, 2, 123)
        assert P.parse(await co.recv()) == P.Sync(P.OUTBOUND, 0)   # nothing accepted outbound
        await fe.send(P.Nonce(P.Id(P.INBOUND, 1, 0), b"n" * 16).pack())
        b = resp(1, 0, a.id, payload=bytes(8))
        await co.send(b.pack()); await settle()
        await fe.recv()
        tk.advance(); await settle()                               # bucket 1 closes
        assert P.parse(await fe.recv()) == P.Sync(P.INBOUND, 1)
        assert P.parse(await co.recv()) == P.Sync(P.OUTBOUND, 1, 0, 123)
        tk.advance(); await settle()                               # bucket 2 closes: certificate for 0..2
        assert P.parse(await fe.recv()) == P.Sync(P.INBOUND, 2)
        assert P.parse(await co.recv()) == P.Sync(P.OUTBOUND, 2)
        cert = P.parse(await fe.recv())
        assert isinstance(cert, P.Certificate) and cert.verify(MAC)
        assert (cert.version, cert.device, cert.bkt_start, cert.bkt_num) == (6, 7, 0, 3)
        assert cert.nonce == b"n" * 16 and cert.chain == bytes(32)
        empty = hashlib.sha256(b"").digest()
        assert cert.inward == P.cert_digest([P.bkt_digest([a.record]), empty, empty])
        assert cert.outward == P.cert_digest([empty, P.bkt_digest([b.record]), empty])
        assert co._in.empty()                                      # certificates go toward the frontend only
        for _ in range(3):
            tk.advance(); await settle()
            await fe.recv(); await co.recv()
        cert2 = P.parse(await fe.recv())
        assert cert2.bkt_start == 3 and cert2.chain == cert.auth_tag and cert2.verify(MAC)
        assert cert2.inward == cert2.outward == P.cert_digest([empty] * 3)
        assert il.certificates_built == 2 and il.closed == 5
        await il.stop()
    run(main())


def test_ticks_never_skip_a_bucket():
    async def main():
        fe, il_fe = MemoryWire.pair()
        co, il_co = MemoryWire.pair()
        tk = ManualTicker()
        il = SoftwareInterlock(P.Params(bkt_num=100), il_fe, il_co, MAC, tk)
        await il.start()
        tk._bucket = 3; tk.advance()                               # the clock jumped to 4: buckets 0..3 all close
        await settle()
        assert [P.parse(await fe.recv()).bucket for _ in range(4)] == [0, 1, 2, 3]
        assert il.closed == 3
        await il.stop()
    run(main())
