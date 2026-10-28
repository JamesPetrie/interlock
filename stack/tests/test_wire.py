import asyncio

import pytest

from ilstack import packet as P
from ilstack.wire import MemoryWire, TcpServerWire, TcpWire, WireClosed

KC = bytes(range(32))
PACKETS = [
    P.Traffic(P.Id(P.INBOUND, 1, 0), P.Id(P.INBOUND, 1, 0), KC, bytes(range(256)) * 5 + b"xy").pack(),  # 1282 bytes
    P.Traffic(P.Id(P.INBOUND, 1, 1), P.Id(P.INBOUND, 1, 0), KC, b"").pack(),                          # empty payload
    P.Sync(P.INBOUND, 1, 0, 42).pack(),
    P.Nonce(P.Id(P.INBOUND, 1, 2), bytes(16)).pack(),
    P.Certificate(6, 0, 0, 10, bytes(16), bytes(32), bytes(32), bytes(32)).packet(),
    P.Traffic(P.Id(P.OUTBOUND, 2, 0), P.Id(P.INBOUND, 1, 1), payload=bytes(1436)).pack(),
]


def test_memory_pair():
    async def main():
        a, b = MemoryWire.pair()
        for pkt in PACKETS:
            await a.send(pkt)
        assert [await b.recv() for _ in PACKETS] == PACKETS
        b.drop = lambda pkt: pkt[4:8] == b"\x00\x00\x00\x02"      # lose syncs
        await b.send(PACKETS[2]); await b.send(PACKETS[0])
        assert await a.recv() == PACKETS[0]
        await a.close()
        with pytest.raises(WireClosed):
            await b.recv()
    asyncio.run(main())


def test_tcp_framing_and_reconnect():
    async def main():
        server = await TcpServerWire.listen("127.0.0.1", 0)
        client = await TcpWire.connect("127.0.0.1", server.port)
        for pkt in PACKETS:                                        # interleave directions
            await client.send(pkt)
            assert await server.recv() == pkt
            await server.send(pkt)
            assert await client.recv() == pkt
        await client.send(b"".join(PACKETS))                        # back-to-back in one write
        assert [await server.recv() for _ in PACKETS] == PACKETS
        await client.close()
        await asyncio.sleep(0.05)                                   # let the hang-up reach the server
        await server.send(PACKETS[0])                               # nobody there: discarded, not an error
        assert server.discarded == 1
        client2 = await TcpWire.connect("127.0.0.1", server.port)
        await client2.send(PACKETS[3])
        assert await server.recv() == PACKETS[3] and server.connections == 2
        await server.send(PACKETS[4])
        assert await client2.recv() == PACKETS[4]
        await server.close()
        with pytest.raises(WireClosed):
            await client2.recv()
    asyncio.run(main())
