import hashlib
import hmac

import pytest

from ilstack import crypto as C
from ilstack import packet as P

MASTER = bytes(range(32))


def test_derive_and_commit_by_hand():
    k0 = C.derive_key(MASTER, 0)
    assert k0 == hmac.new(MASTER, b"interlock pair key v1" + bytes(8), hashlib.sha256).digest()
    assert C.derive_key(MASTER, 1) != k0 and len(k0) == 32
    assert C.commit(k0) == hashlib.sha256(k0).digest()
    with pytest.raises(ValueError):
        C.derive_key(b"short", 0)


def test_crypt_roundtrip_and_direction_separation():
    key = C.derive_key(MASTER, 3)
    data = bytes(range(100))
    ct = C.crypt(key, P.INBOUND, 0, data)
    assert ct != data and C.crypt(key, P.INBOUND, 0, ct) == data
    assert C.crypt(key, P.OUTBOUND, 0, data) != ct
    assert C.crypt(C.derive_key(MASTER, 4), P.INBOUND, 0, data) != ct


def test_crypt_is_position_addressable():
    key = C.derive_key(MASTER, 7)
    msg = bytes(i % 251 for i in range(3000))
    whole = C.crypt(key, P.INBOUND, 0, msg)
    # packets continue the keystream where the previous one stopped
    assert C.crypt(key, P.INBOUND, 0, msg[:1436]) + C.crypt(key, P.INBOUND, 1436, msg[1436:]) == whole
    # one token's ciphertext depends only on its own position
    for i in (0, 1, 359, 749):
        assert C.crypt(key, P.INBOUND, 4 * i, msg[4 * i:4 * i + 4]) == whole[4 * i:4 * i + 4]
    assert C.crypt(key, P.INBOUND, 17, msg[17:40]) == whole[17:40]          # unaligned offsets too


def test_pair_keys_table():
    fe, co = C.PairKeys(MASTER), C.PairKeys(MASTER, lookahead=8)
    pairs = [fe.new_pair() for _ in range(5)]
    assert [p.counter for p in pairs] == [0, 1, 2, 3, 4]
    assert co.lookup(pairs[2].commit) == pairs[2]                            # out of order is fine
    assert co.lookup(pairs[0].commit) == pairs[0]                            # older than the newest match, still in view
    assert co.lookup(pairs[4].commit) == pairs[4]
    assert co.lookup(hashlib.sha256(b"nope").digest()) is None
    far = fe.pair(4 + 8 + 1)                                                 # beyond the lookahead from the newest match
    assert co.lookup(far.commit) is None
    near = fe.pair(4 + 8)
    assert co.lookup(near.commit) == near                                    # the window moves with each match
    assert co.lookup(far.commit) == far


def test_pair_keys_forgets_old_counters():
    co = C.PairKeys(MASTER, lookahead=4)

    def c(n):
        return C.commit(C.derive_key(MASTER, n))

    assert co.lookup(c(3)) is not None                                       # in the first window, 0..3
    assert co.lookup(c(20)) is None                                          # beyond it
    assert co.lookup(c(7)) is not None                                       # the window moved to 4..7
    assert co.lookup(c(4)) is not None                                       # within lookahead behind the newest match
    assert co.lookup(c(0)) is None                                           # older than that: forgotten
