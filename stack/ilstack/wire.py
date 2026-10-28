"""Wires: how packets are carried. Nothing above knows which one is in use.

A wire carries whole packets, in order, both ways: `send(packet)` and
`recv() -> packet`. `MemoryWire.pair()` gives two connected ends for tests.
`TcpWire` is the TCP tunnel of the first phases, each packet framed by its
own length field (`packet.frame_length`). A tap wire comes later with the
same interface.
"""
from __future__ import annotations

import asyncio

from .packet import frame_length


class Wire:
    async def send(self, packet: bytes) -> None:
        raise NotImplementedError

    async def recv(self) -> bytes:
        raise NotImplementedError

    async def close(self) -> None:
        pass


class WireClosed(ConnectionError):
    """The other end is gone for good."""


class MemoryWire(Wire):
    """One end of an in-process channel. `drop(packet) -> bool` lets tests lose packets."""

    def __init__(self, inbox: asyncio.Queue, outbox: asyncio.Queue):
        self._in, self._out = inbox, outbox
        self.sent = 0
        self.drop = None

    @classmethod
    def pair(cls) -> tuple["MemoryWire", "MemoryWire"]:
        a, b = asyncio.Queue(), asyncio.Queue()
        return cls(a, b), cls(b, a)

    async def send(self, packet: bytes) -> None:
        self.sent += 1
        if self.drop is not None and self.drop(packet):
            return
        await self._out.put(packet)

    async def recv(self) -> bytes:
        pkt = await self._in.get()
        if pkt is None:
            raise WireClosed
        return pkt

    async def close(self) -> None:
        await self._out.put(None)


async def _read_packet(reader: asyncio.StreamReader) -> bytes:
    head = await reader.readexactly(8)
    return head + await reader.readexactly(frame_length(head) - 8)


class TcpWire(Wire):
    """The connecting end of a TCP link (the frontend and compute connect to
    the interlock)."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self._r, self._w = reader, writer

    @classmethod
    async def connect(cls, host: str, port: int, retries: int = 50, delay: float = 0.2) -> "TcpWire":
        for attempt in range(retries):
            try:
                r, w = await asyncio.open_connection(host, port)
                return cls(r, w)
            except OSError:
                if attempt == retries - 1:
                    raise
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    async def send(self, packet: bytes) -> None:
        self._w.write(packet)
        await self._w.drain()

    async def recv(self) -> bytes:
        try:
            return await _read_packet(self._r)
        except (asyncio.IncompleteReadError, ConnectionError, OSError) as e:
            raise WireClosed from e

    async def close(self) -> None:
        self._w.close()
        try:
            await self._w.wait_closed()
        except Exception:
            pass


class TcpServerWire(Wire):
    """The listening end (the interlock): one client at a time, and a new
    client replaces one that went away, so the nodes may restart while the
    interlock keeps running. `send` with nobody connected discards the packet
    and counts it, like a packet sent into a dead link; `recv` waits for the
    next client after a hang-up."""

    def __init__(self):
        self._r: asyncio.StreamReader | None = None
        self._w: asyncio.StreamWriter | None = None
        self._connected = asyncio.Event()
        self._server: asyncio.AbstractServer | None = None
        self.connections = 0
        self.discarded = 0

    @classmethod
    async def listen(cls, host: str, port: int) -> "TcpServerWire":
        wire = cls()
        wire._server = await asyncio.start_server(wire._on_conn, host, port)
        return wire

    @property
    def port(self) -> int:
        return self._server.sockets[0].getsockname()[1]

    async def _on_conn(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        if self._w is not None:
            self._w.close()
        self._r, self._w = r, w
        self.connections += 1
        self._connected.set()

    async def send(self, packet: bytes) -> None:
        if self._w is None or self._w.is_closing() or self._r.at_eof():
            self.discarded += 1
            return
        try:
            self._w.write(packet)
            await self._w.drain()
        except (ConnectionError, OSError):
            self.discarded += 1
            self._drop_client()

    async def recv(self) -> bytes:
        while True:
            await self._connected.wait()
            try:
                return await _read_packet(self._r)
            except (asyncio.IncompleteReadError, ConnectionError, OSError):
                self._drop_client()

    def _drop_client(self) -> None:
        if self._w is not None:
            self._w.close()
        self._r = self._w = None
        self._connected.clear()

    async def close(self) -> None:
        self._drop_client()
        if self._server is not None:
            self._server.close()
