from ilstack import crypto, messages as M, packet as P
from ilstack.assembly import Assembler, encrypt

MASTER = bytes(range(32))
KEYS = crypto.PairKeys(MASTER)


def chain(direction, bucket, payloads, key_commit=P.NO_KEY, reference=None):
    """Packets of one message, consecutive slots in one bucket."""
    out, prev = [], reference
    for slot, payload in enumerate(payloads):
        id = P.Id(direction, bucket, slot)
        out.append(P.Traffic(id, prev if prev is not None else id, key_commit, payload))
        prev = id
    return out


def test_single_and_multi_packet_messages():
    pair = KEYS.new_pair()
    co = Assembler(P.INBOUND, lambda pkt: KEYS.lookup(pkt.key_commit).key)
    body = M.encode(M.Request([7, 8, 9], {"max_tokens": 4}))
    (pkt,) = chain(P.INBOUND, 1, encrypt(pair.key, P.INBOUND, body, 1436), pair.commit)
    msg = co.feed(pkt)
    assert msg is not None and msg.body == body and M.decode(msg.body) == M.Request([7, 8, 9], {"max_tokens": 4})
    assert (msg.first, msg.last, msg.reference, msg.key, msg.key_commit) == (pkt.id, pkt.id, pkt.id, pair.key, pair.commit)
    big = M.encode(M.Request(list(range(2000))))
    pkts = chain(P.INBOUND, 2, encrypt(pair.key, P.INBOUND, big, 1436), pair.commit)
    assert len(pkts) == 6
    assert all(co.feed(p) is None for p in pkts[:-1])
    msg = co.feed(pkts[-1])
    assert msg.body == big and msg.first == pkts[0].id and msg.last == pkts[-1].id
    assert co.stats.messages == 2 and not co._open


def test_response_keyed_by_the_request_it_references():
    pair = KEYS.new_pair()
    req_last = P.Id(P.INBOUND, 5, 3)
    fe = Assembler(P.OUTBOUND, lambda pkt: pair.key if pkt.reference == req_last else None)
    body = M.encode(M.Response([1, 2, 3], "length"))
    pkts = chain(P.OUTBOUND, 6, encrypt(pair.key, P.OUTBOUND, body, 8), reference=req_last)
    assert len(pkts) > 1
    for p in pkts[:-1]:
        assert fe.feed(p) is None
    msg = fe.feed(pkts[-1])
    assert M.decode(msg.body) == M.Response([1, 2, 3], "length") and msg.reference == req_last
    stray = chain(P.OUTBOUND, 7, [bytes(16)], reference=P.Id(P.INBOUND, 5, 2))[0]
    assert fe.feed(stray) is None and fe.stats.unknown_key == 1


def test_interleaved_chains_and_malformed():
    p1, p2 = KEYS.new_pair(), KEYS.new_pair()
    co = Assembler(P.INBOUND, lambda pkt: KEYS.lookup(pkt.key_commit).key)
    b1, b2 = M.encode(M.Request(list(range(10)))), M.encode(M.Request(list(range(20, 30))))
    c1 = chain(P.INBOUND, 1, encrypt(p1.key, P.INBOUND, b1, 20), p1.commit)
    c2 = chain(P.INBOUND, 2, encrypt(p2.key, P.INBOUND, b2, 20), p2.commit)
    assert len(c1) == 3 and len(c2) == 3
    order = [c1[0], c2[0], c1[1], c2[1], c2[2], c1[2]]
    got = [co.feed(p) for p in order]
    assert [m.body if m else None for m in got] == [None, None, None, None, b2, b1]
    # a payload that does not decrypt to a message prefix
    bad = chain(P.INBOUND, 3, [bytes(16)], p1.commit)[0]
    assert co.feed(bad) is None and co.stats.malformed == 1
    # a continuation that overruns the announced length
    over = [crypto.crypt(p1.key, P.INBOUND, 0, b1[:20]), crypto.crypt(p1.key, P.INBOUND, 20, b1[20:] + bytes(4))]
    assert [co.feed(p) for p in chain(P.INBOUND, 4, over, p1.commit)] == [None, None] and co.stats.malformed == 2
    # a first payload shorter than the prefix is fine: the length is read once it is in
    tiny = chain(P.INBOUND, 5, encrypt(p2.key, P.INBOUND, b2, 4), p2.commit)
    assert [co.feed(p) for p in tiny[:-1]] == [None] * (len(tiny) - 1) and co.feed(tiny[-1]).body == b2


def test_open_messages_are_bounded():
    pair = KEYS.new_pair()
    co = Assembler(P.INBOUND, lambda pkt: pair.key, max_open=2)
    body = M.encode(M.Request(list(range(10))))
    for b in range(4):
        co.feed(chain(P.INBOUND, b, encrypt(pair.key, P.INBOUND, body, 20), pair.commit)[0])
    assert len(co._open) == 2 and co.stats.expired == 2
