import asyncio

from harness import MAC, ManualTicker, run, settle

from ilstack import messages as M, packet as P
from ilstack.clock import SyncClock
from ilstack.compute import ComputeNode
from ilstack.crypto import PairKeys
from ilstack.dispatcher import Dispatcher
from ilstack.engines import MockEngine
from ilstack.frontend import Frontend
from ilstack.interlock import RealTicker, SoftwareInterlock
from ilstack.log import MemoryLog
from ilstack.wire import MemoryWire

MASTER = bytes(range(32))


class Stack:
    """Frontend and compute through a software interlock on memory wires."""

    def __init__(self, params=P.Params(num_slot=8, bkt_num=20, bucket_ms=20), real=True, timeout=2.0, retries=1,
                 engine=None):
        self.params = params
        self.fe_wire, il_fe = MemoryWire.pair()
        self.co_wire, il_co = MemoryWire.pair()
        self.ticker = RealTicker(params.bucket_ms) if real else ManualTicker()
        clocks = [SyncClock(params.bucket_ms) for _ in range(2)]
        self.il = SoftwareInterlock(params, il_fe, il_co, MAC, self.ticker)
        self.fe_log = MemoryLog()
        self.fe = Frontend(params, Dispatcher(params, self.fe_wire, clocks[0], self.fe_log, P.INBOUND),
                           PairKeys(MASTER), self.fe_log, timeout=timeout, retries=retries)
        self.engine = engine or MockEngine()
        self.co = ComputeNode(params, Dispatcher(params, self.co_wire, clocks[1], MemoryLog(), P.OUTBOUND),
                              PairKeys(MASTER), self.engine)

    async def __aenter__(self):
        await self.il.start()
        await self.fe.start()
        await self.co.start()
        return self

    async def __aexit__(self, *exc):
        await self.fe.stop()
        await self.co.stop()
        await self.il.stop()


def test_completions_end_to_end():
    async def main():
        async with Stack() as s:
            resp = await s.fe.complete([1, 2, 3, 4], {"max_tokens": 8})
            assert resp == M.Response([4, 3, 2, 1], "stop")
            long = list(range(1, 3001))                          # several packets each way
            resp = await s.fe.complete(long, {"max_tokens": 2000})
            assert resp.tokens == long[::-1][:2000] and resp.finish_reason == "length"
            results = await asyncio.gather(*(s.fe.complete([i] * 5, {"max_tokens": 5}) for i in range(1, 9)))
            assert [r.tokens for r in results] == [[i] * 5 for i in range(1, 9)]
            assert s.fe.stats.completions == 10 and s.fe.stats.retries == 0 and s.co.stats.responses == 10
            assert s.il.counters[P.INBOUND].drops == 0 and s.il.counters[P.OUTBOUND].drops == 0
            assert len(s.fe_log.certificates) >= 0 and s.fe_log.counter(s.fe.keys.pair(0).commit) == 0
    run(main())


def test_lost_request_is_sent_again():
    async def main():
        async with Stack() as s:
            once = [True]

            def drop_first_root(raw):                            # lose the first request's first packet, once
                if once[0] and raw[4:8] == b"\x00\x00\x00\x0F":
                    once[0] = False
                    return True
                return False
            s.fe_wire.drop = drop_first_root
            resp = await s.fe.complete([1, 2, 3], {"max_tokens": 4})
            assert resp == M.Response([3, 2, 1], "stop")
            assert s.fe.stats.retries == 1 and s.fe.stats.lost == 1 and s.fe.stats.completions == 1
            assert s.co.assembler.stats.messages == 1            # the half request never completed on compute
    run(main())


def test_no_answer_gives_an_error_response():
    async def main():
        async with Stack(timeout=0.3, retries=1) as s:
            s.co_wire.drop = lambda raw: raw[4:8] == b"\x00\x00\x00\x0F"      # every response is lost
            resp = await s.fe.complete([1, 2, 3], {"max_tokens": 4})
            assert resp.finish_reason == "error" and s.fe.stats.failures == 1
            assert s.fe.stats.lost + s.fe.stats.timeouts == 2 and s.co.stats.lost_responses == 2
    run(main())


def test_nonce_round_trip():
    async def main():
        async with Stack(P.Params(num_slot=8, bkt_num=5, bucket_ms=10)) as s:
            cert = await s.fe.nonce(b"v" * 16, timeout=2)
            assert cert.nonce == b"v" * 16 and cert.verify(MAC) and cert in s.fe_log.certificates
    run(main())


def test_bad_request_gets_an_error_not_a_timeout():
    async def main():
        async with Stack() as s:
            pair = s.fe.keys.new_pair()
            from ilstack.assembly import encrypt
            from ilstack.frontend import _Pending
            pending = _Pending(pair, asyncio.get_running_loop().create_future())
            body = M.encode(M.Response([1], "stop"))             # a response where a request belongs
            s.fe.dispatcher.send(encrypt(pair.key, P.INBOUND, body, s.params.s_max), pair.commit, tag=pending)
            resp = await asyncio.wait_for(pending.future, 2)
            assert resp.finish_reason == "error" and "not a request" in resp.error and s.co.stats.errors == 1
    run(main())
