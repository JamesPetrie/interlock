"""The frontend node's core: token ids in, token ids out, everything between
done by the stages below.

A completion takes a fresh pair key, encodes and encrypts the request, and
hands the payloads to the dispatcher with the pair as their tag. The
response's packets reference the request's last packet, so the assembler
finds the pair, and so the key, through the dispatcher's tags. A request the
interlock dropped (the sync says so) or that gets no answer in time is sent
again under a new pair, a bounded number of times; what the frontend learns
either way it counts.

Every certificate that arrives is audited against the log: if the log does
not reproduce it, the interlock dropped something the sync did not reveal,
and a challenge into that interval would fail. When the sender left
suspects in that interval (packets whose send returned after their bucket
closed), the audit tries the certificate without them; the ones it was
missing are lost after all and their requests go again at once (a
workaround for VM host stalls, see `Dispatcher`). What no suspect explains
is counted.

The verifier's nonce goes out as a nonce packet; the certificate that echoes
it comes back through the dispatcher.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from . import messages as M, packet as P
from .assembly import Assembler, encrypt
from .crypto import Pair, PairKeys
from .dispatcher import Dispatcher
from .log import Log
from .verifier import audit, resolve

log = logging.getLogger(__name__)


class Lost(Exception):
    """The interlock dropped a packet of the request."""


@dataclass
class _Pending:
    pair: Pair
    future: asyncio.Future


@dataclass
class FrontendStats:
    completions: int = 0
    retries: int = 0
    lost: int = 0
    timeouts: int = 0
    failures: int = 0
    unknown_responses: int = 0
    certificates_ok: int = 0
    certificates_mismatched: int = 0
    certificates_resolved: int = 0     # matched once suspects the interlock did not take were dropped


class Frontend:
    def __init__(self, params: P.Params, dispatcher: Dispatcher, keys: PairKeys, log_: Log,
                 timeout: float = 60.0, retries: int = 1):
        self.params, self.dispatcher, self.keys, self.log = params, dispatcher, keys, log_
        self.timeout, self.retries = timeout, retries
        self.stats = FrontendStats()
        self.assembler = Assembler(P.OUTBOUND, self._key_for)
        self.dispatcher.certificate_listeners.append(self._audit)
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        await self.dispatcher.start()
        self._tasks = [asyncio.create_task(self._responses()), asyncio.create_task(self._losses())]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        await self.dispatcher.stop()

    # --- requests ----------------------------------------------------------

    async def complete(self, tokens: list[int], params: dict | None = None) -> M.Response:
        body = M.encode(M.Request(tokens, params or {}))
        for attempt in range(self.retries + 1):
            pair = self.keys.new_pair()
            self.log.key(pair)
            pending = _Pending(pair, asyncio.get_running_loop().create_future())
            sent = self.dispatcher.send(encrypt(pair.key, P.INBOUND, body, self.params.s_max), pair.commit, tag=pending)
            try:
                resp = await asyncio.wait_for(pending.future, self.timeout)
                self.stats.completions += 1
                return resp
            except Lost:
                self.stats.lost += 1
            except asyncio.TimeoutError:
                self.stats.timeouts += 1
            finally:
                self.dispatcher.forget(sent)
            if attempt < self.retries:
                self.stats.retries += 1
                log.info("request under pair %d got no answer, sending again", pair.counter)
        self.stats.failures += 1
        return M.Response([], "error", "no answer from compute")

    def _key_for(self, pkt: P.Traffic) -> bytes | None:
        pending = self.dispatcher.tags.get(pkt.reference)
        return pending.pair.key if isinstance(pending, _Pending) else None

    async def _responses(self) -> None:
        while True:
            pkt = await self.dispatcher.incoming.get()
            msg = self.assembler.feed(pkt)
            if msg is None:
                continue
            pending = self.dispatcher.tags.get(msg.reference)
            if not isinstance(pending, _Pending) or pending.future.done():
                self.stats.unknown_responses += 1
                continue
            try:
                pending.future.set_result(M.decode(msg.body))
            except ValueError as e:
                pending.future.set_result(M.Response([], "error", f"malformed response: {e}"))

    async def _losses(self) -> None:
        while True:
            id, tag = await self.dispatcher.lost.get()
            if isinstance(tag, _Pending) and not tag.future.done():
                tag.future.set_exception(Lost(id))

    def _audit(self, cert: P.Certificate) -> None:
        res = audit(self.params, self.log, cert)
        suspects = [] if all(res.values()) else self.dispatcher.suspects_in(cert)
        gone = resolve(self.params, self.log, cert, suspects) if suspects else None
        if all(res.values()):
            self.stats.certificates_ok += 1
        elif gone is not None:
            self.stats.certificates_ok += 1
            self.stats.certificates_resolved += 1
            log.info("certificate for buckets %d..%d matches without %d of %d suspects", cert.bkt_start,
                     cert.bkt_start + cert.bkt_num - 1, len(gone), len(suspects))
            self.dispatcher.not_accepted(gone)                     # lost after all: their requests go again now
        else:
            self.stats.certificates_mismatched += 1
            log.warning("certificate for buckets %d..%d does not match the log: %s", cert.bkt_start,
                        cert.bkt_start + cert.bkt_num - 1, [k for k, ok in res.items() if not ok])

    # --- the verifier's nonce ----------------------------------------------

    async def nonce(self, nonce: bytes, timeout: float | None = None) -> P.Certificate:
        """Send the nonce; return the next certificate that echoes it."""
        waiter = asyncio.create_task(self.dispatcher.next_certificate(lambda c: c.nonce == nonce))
        self.dispatcher.nonce(nonce)
        return await asyncio.wait_for(waiter, timeout)
