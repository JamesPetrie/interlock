import pytest

from ilstack import messages as M


def test_request_roundtrip_and_units():
    req = M.Request([1, 2, 3, 0xFFFFFFFF], {"max_tokens": 16, "temperature": 0.5})
    body = M.encode(req)
    assert len(body) % M.UNIT == 0
    assert body[:16] == (b"\x00\x00\x00\x01" b"\x00\x00\x00\x01"                  # version, kind
                         + len(body[16:-16]).to_bytes(4, "big") + b"\x00\x00\x00\x04")
    assert body[-16:] == b"\x00\x00\x00\x01\x00\x00\x00\x02\x00\x00\x00\x03\xFF\xFF\xFF\xFF"
    assert M.decode(body) == req
    assert M.total_length(body[:16]) == len(body)


def test_response_roundtrip():
    for resp in (M.Response([5, 6]), M.Response([], "error", "unknown reference"), M.Response(list(range(1000)), "length")):
        body = M.encode(resp)
        assert len(body) % M.UNIT == 0 and M.decode(body) == resp
    assert M.decode(M.encode(M.Request([]))) == M.Request([])


def test_decode_rejects():
    body = M.encode(M.Request([1, 2]))
    with pytest.raises(ValueError):
        M.decode(body[:-4])
    with pytest.raises(ValueError):
        M.decode(body + bytes(4))
    with pytest.raises(ValueError):
        M.total_length(b"\x00\x00\x00\x02" + body[4:16])          # unknown format version
    with pytest.raises(ValueError):
        M.encode(M.Request([-1]))


def test_split():
    body = M.encode(M.Request(list(range(1000))))                  # 16 + 4 + 4000 bytes (meta "{}" padded)
    chunks = M.split(body, 1436)
    assert [len(c) for c in chunks] == [1436, 1436, 1148]
    assert b"".join(chunks) == body and all(len(c) % M.UNIT == 0 for c in chunks[:-1])
    assert M.split(b"", 1436) == [b""]
    assert [len(c) for c in M.split(bytes(12), 10)] == [8, 4]    # s_max rounded down to whole units
