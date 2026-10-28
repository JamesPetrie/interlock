"""The compute node's core: requests out of the packets, responses back in.

Packets from the dispatcher go through the assembler, which finds each
request's key by its KEY_COMMIT. A complete request is decoded and handed
to the engine; the response is encoded, encrypted under the same pair key
in the outbound direction, and sent referencing the request's last packet.
Requests run concurrently up to a limit; anything that fails to decode gets
an error response so the frontend does not wait for a timeout.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from . import messages as M, packet as P
from .assembly import Assembler, Message, encrypt
from .crypto import PairKeys
from .dispatcher import Dispatcher
from .engines import Engine

log = logging.getLogger(__name__)


@dataclass
class ComputeStats:
    requests: int = 0
    responses: int = 0
    errors: int = 0
    lost_responses: int = 0


class ComputeNode:
    def __init__(self, params: P.Params, dispatcher: Dispatcher, keys: PairKeys, engine: Engine,
                 concurrency: int = 16):
        self.params, self.dispatcher, self.keys, self.engine = params, dispatcher, keys, engine
        self.stats = ComputeStats()
        self.assembler = Assembler(P.INBOUND, self._key_for)
        self._limit = asyncio.Semaphore(concurrency)
        self._tasks: list[asyncio.Task] = []
        self._jobs: set[asyncio.Task] = set()

    async def start(self) -> None:
        await self.dispatcher.start()
        self._tasks = [asyncio.create_task(self._requests()), asyncio.create_task(self._losses())]

    async def stop(self) -> None:
        for t in self._tasks + list(self._jobs):
            t.cancel()
        for t in self._tasks + list(self._jobs):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        await self.dispatcher.stop()

    def _key_for(self, pkt: P.Traffic) -> bytes | None:
        pair = self.keys.lookup(pkt.key_commit)
        return pair.key if pair else None

    async def _requests(self) -> None:
        while True:
            pkt = await self.dispatcher.incoming.get()
            msg = self.assembler.feed(pkt)
            if msg is not None:
                job = asyncio.create_task(self._serve(msg))
                self._jobs.add(job)
                job.add_done_callback(self._jobs.discard)

    async def _serve(self, msg: Message) -> None:
        self.stats.requests += 1
        async with self._limit:
            try:
                req = M.decode(msg.body)
                if not isinstance(req, M.Request):
                    raise ValueError("not a request")
                resp = await self.engine.generate(req.tokens, req.params)
            except Exception as e:
                log.warning("request %s failed: %s", msg.first, e)
                self.stats.errors += 1
                resp = M.Response([], "error", str(e)[:200])
        self.dispatcher.send(encrypt(msg.key, P.OUTBOUND, M.encode(resp), self.params.s_max),
                             reference=msg.last, tag=msg.first)
        self.stats.responses += 1

    async def _losses(self) -> None:
        while True:
            id, tag = await self.dispatcher.lost.get()
            self.stats.lost_responses += 1
            log.info("response packet %s for request %s was dropped; the frontend will ask again", id, tag)
