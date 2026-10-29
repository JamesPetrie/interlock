"""Shared test plumbing: a hand-driven interlock clock, and two dispatchers
linked through a software interlock over memory wires."""
import asyncio
from dataclasses import dataclass

from ilstack import packet as P
from ilstack.clock import ManualClock, SyncClock
from ilstack.compute import ComputeNode
from ilstack.crypto import PairKeys
from ilstack.dispatcher import Dispatcher
from ilstack.engines import MockEngine
from ilstack.frontend import Frontend
from ilstack.interlock import RealTicker, SoftwareInterlock, Ticker
from ilstack.log import MemoryLog
from ilstack.wire import MemoryWire

MAC = b"m" * 32
MASTER = bytes(range(32))


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


async def settle(n: int = 8):
    for _ in range(n):
        await asyncio.sleep(0)


def run(coro, timeout: float = 10):
    asyncio.run(asyncio.wait_for(coro, timeout))


@dataclass
class Link:
    params: P.Params
    fe: Dispatcher                 # inbound sender (the frontend's)
    co: Dispatcher                 # outbound sender (compute's)
    il: SoftwareInterlock
    fe_wire: MemoryWire            # the frontend's end of its wire (drop hooks go here)
    co_wire: MemoryWire
    ticker: Ticker
    clocks: tuple

    async def advance(self, n: int = 1):
        """Hand-driven links only: move the interlock and both senders on by n buckets."""
        for _ in range(n):
            self.ticker.advance()
            for c in self.clocks:
                c.advance()
            await settle()

    async def stop(self):
        await self.fe.stop()
        await self.co.stop()
        await self.il.stop()


async def make_link(params: P.Params = P.Params(), manual: bool = True) -> Link:
    fe_wire, il_fe = MemoryWire.pair()
    co_wire, il_co = MemoryWire.pair()
    if manual:
        ticker = ManualTicker()
        clocks = (ManualClock(), ManualClock())
    else:
        ticker = RealTicker(params.bucket_ms)
        clocks = (SyncClock(params.bucket_ms), SyncClock(params.bucket_ms))
    il = SoftwareInterlock(params, il_fe, il_co, MAC, ticker)
    fe = Dispatcher(params, fe_wire, clocks[0], MemoryLog(), P.INBOUND)
    co = Dispatcher(params, co_wire, clocks[1], MemoryLog(), P.OUTBOUND)
    await il.start()
    await fe.start()
    await co.start()
    return Link(params, fe, co, il, fe_wire, co_wire, ticker, clocks)


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
