"""Byte-exact checks of the wire layout against docs/verification-protocol.md."""
import hashlib
import hmac

import pytest

from ilstack import packet as P

KC = bytes(range(32))                       # a KEY_COMMIT
REQ_ID = P.Id(P.INBOUND, 0x01020304, 0x0005)
RESP_ID = P.Id(P.OUTBOUND, 0x01020305, 0x0000)


def test_request_layout():
    raw = P.Traffic(REQ_ID, REQ_ID, KC, b"\x11\x22\x33\x44").pack()
    assert raw == (b"\x00\x00\x00\x04"                       # PLD_LEN
                   b"\x00\x00\x00\x0F"                       # TYPE traffic
                   b"\x00\x00"                               # DIR inbound
                   b"\x01\x02\x03\x04"                       # BUCKET
                   b"\x00\x05"                               # SLOT
                   b"\x00\x00\x01\x02\x03\x04\x00\x05"       # REFERENCE: itself (a root)
                   + bytes(8)                                # RESERVED
                   + KC                                      # KEY_COMMIT
                   + b"\x11\x22\x33\x44")                    # PAYLOAD
    assert len(raw) == P.HEADER_SIZE + 4


def test_response_layout():
    raw = P.Traffic(RESP_ID, REQ_ID, payload=b"\xAA" * 8).pack()
    assert raw == (b"\x00\x00\x00\x08" b"\x00\x00\x00\x0F"
                   b"\xFF\xFF" b"\x01\x02\x03\x05" b"\x00\x00"
                   b"\x00\x00\x01\x02\x03\x04\x00\x05"       # REFERENCE: the request
                   + bytes(8) + bytes(32)                    # RESERVED, RESERVED (no KEY_COMMIT)
                   + b"\xAA" * 8)


def test_sync_layout():
    raw = P.Sync(P.OUTBOUND, 7, 3, 1234).pack()
    assert raw == (bytes(4) + b"\x00\x00\x00\x02" + b"\xFF\xFF" + b"\x00\x00\x00\x07" + b"\x00\x03"
                   + bytes(8) + (1234).to_bytes(8, "big") + bytes(32))
    empty = P.Sync(P.INBOUND, 7).pack()
    assert empty[14:16] == b"\xFF\xFF" and empty[24:32] == b"\xFF" * 8
    assert len(raw) == P.HEADER_SIZE


def test_nonce_layout():
    nonce = bytes(range(16))
    raw = P.Nonce(P.Id(P.INBOUND, 9, 2), nonce).pack()
    assert raw == bytes(4) + b"\x00\x00\x00\x01" + b"\x00\x00" + b"\x00\x00\x00\x09" + b"\x00\x02" + bytes(32) + nonce


def test_certificate_layout_and_tag():
    key = b"k" * 32
    c = P.Certificate(6, 1, 3000, 1000, bytes(range(16)), b"i" * 32, b"o" * 32, b"c" * 32).signed(key)
    raw = c.pack()
    body = (b"\x00\x00\x00\x06" b"\x00\x00\x00\x01" b"\x00\x00\x0B\xB8" b"\x00\x00\x03\xE8"
            + bytes(range(16)) + b"i" * 32 + b"o" * 32 + b"c" * 32)
    assert raw[:128] == body
    assert raw[128:] == hmac.new(key, body, hashlib.sha256).digest()
    assert len(raw) == P.CERTIFICATE_SIZE
    assert c.verify(key) and not c.verify(b"x" * 32)
    assert P.Certificate.unpack(raw) == c
    pkt = c.packet()
    assert pkt[:64] == bytes(64) and pkt[4:8] == b"\x00\x00\x00\x00" and len(pkt) == 224
    assert P.parse(pkt) == c
    assert c.covers(3000) and c.covers(3999) and not c.covers(4000) and not c.covers(2999)


def test_id_packing_and_order():
    assert REQ_ID.pack() == 0x0000_01020304_0005
    assert RESP_ID.pack() == 0xFFFF_01020305_0000
    assert P.Id.unpack(REQ_ID.pack()) == REQ_ID
    assert P.Id.unpack(RESP_ID.pack()) == RESP_ID
    assert P.Id(P.INBOUND, 5, 60).order < P.Id(P.INBOUND, 6, 0).order
    with pytest.raises(ValueError):
        P.Id(0x1234, 0, 0).pack()
    with pytest.raises(ValueError):
        P.Id(P.INBOUND, 1 << 32, 0).pack()


def test_reference_rule():
    me = P.Id(P.INBOUND, 10, 3)
    assert P.reference_ok(me, me)                               # a root references itself
    assert P.reference_ok(P.Id(P.INBOUND, 10, 2), me)
    assert P.reference_ok(P.Id(P.INBOUND, 9, 63), me)
    assert not P.reference_ok(P.Id(P.INBOUND, 10, 4), me)       # later slot
    assert not P.reference_ok(P.Id(P.INBOUND, 11, 0), me)       # later bucket
    assert P.reference_ok(P.Id(P.OUTBOUND, 9, 0), me)           # other direction: earlier bucket
    assert not P.reference_ok(P.Id(P.OUTBOUND, 10, 0), me)      # other direction: same bucket is too late
    assert not P.reference_ok(P.Id(0x1234, 9, 0), me)


def test_parse_roundtrip():
    for pkt in (P.Traffic(REQ_ID, REQ_ID, KC, bytes(8)), P.Traffic(RESP_ID, REQ_ID, payload=bytes(1436)),
                P.Sync(P.INBOUND, 7, 3, 1234), P.Sync(P.OUTBOUND, 8), P.Nonce(P.Id(P.INBOUND, 9, 2), bytes(16))):
        assert P.parse(pkt.pack()) == pkt


def test_parse_rejects():
    good = P.Traffic(REQ_ID, REQ_ID, KC, bytes(8)).pack()

    def reason(raw):
        with pytest.raises(P.FormatError) as e:
            P.parse(raw)
        return e.value.reason

    assert reason(good[:63]) == "short"
    assert reason(good[:-1]) == "length"
    assert reason(good + b"\0") == "length"
    assert reason(good[:4] + b"\x00\x00\x00\x03" + good[8:]) == "type"
    assert reason(good[:4] + b"\x00\x00\x01\x0F" + good[8:]) == "type"        # upper bits of TYPE
    assert reason(good[:8] + b"\x12\x34" + good[10:]) == "dir"
    assert reason(good[:24] + b"\0" * 7 + b"\1" + good[32:]) == "reserved"
    resp = P.Traffic(RESP_ID, REQ_ID, payload=bytes(8)).pack()
    assert reason(resp[:32] + KC + resp[64:]) == "reserved"                    # a response carries no KEY_COMMIT
    sync = P.Sync(P.INBOUND, 7).pack()
    assert reason(sync[:16] + b"\1" + sync[17:]) == "reserved"
    assert reason(sync[:1] + b"\1" + sync[2:]) == "length"                     # PLD_LEN without payload
    cert = P.Certificate(6, 0, 0, 10, bytes(16), bytes(32), bytes(32), bytes(32)).packet()
    assert reason(cert[:10] + b"\1" + cert[11:]) == "certificate"
    assert reason(cert[:-1]) == "certificate"


def test_frame_length():
    assert P.frame_length(P.Traffic(REQ_ID, REQ_ID, KC, bytes(100)).pack()[:8]) == 164
    assert P.frame_length(P.Sync(P.INBOUND, 1).pack()[:8]) == 64
    assert P.frame_length(bytes(8)) == 224                                      # certificate packet


def test_digests_by_hand():
    t = P.Traffic(REQ_ID, REQ_ID, KC, b"\x11\x22\x33\x44")
    pld = hashlib.sha256(b"\x11\x22\x33\x44").digest()
    pkt = hashlib.sha256(t.header + pld).digest()
    assert P.pld_digest(t.payload) == pld
    assert P.pkt_digest(t.header, pld) == pkt
    assert t.record == b"\x00\x04" + pkt and len(t.record) == 34
    assert P.record(0x12345, pkt) == b"\x23\x45" + pkt                         # PLD_LEN[15:0]
    assert P.bkt_digest([]) == hashlib.sha256(b"").digest()
    r2 = P.Traffic(P.Id(P.INBOUND, 0x01020304, 6), REQ_ID, KC, bytes(4)).record
    assert P.bkt_digest([t.record, r2]) == hashlib.sha256(t.record + r2).digest()
    b1, b2 = P.bkt_digest([]), P.bkt_digest([t.record])
    assert P.cert_digest([b1, b2]) == hashlib.sha256(b1 + b2).digest()


def test_params():
    p = P.Params()
    assert p.num_slot == 64 and p.bkt_num == 1000 and p.s_max == 1436 and p.token_unit == 4
    assert p.capacity == 64 * 1436
