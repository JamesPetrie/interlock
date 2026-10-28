"""Keys and the payload cipher: the only code that touches keys.

One key per request/response pair (protocol spec decision 1). The frontend
derives it from the master secret it shares with compute and a counter, and
commits to it in every packet of the request: KEY_COMMIT = H(key). Compute
recognises the key by its commitment, deriving keys for the counters ahead of
the last one it matched. Nothing about keys crosses the wire.

The cipher is AES-256-CTR over a fixed 4-byte token unit (spec decision 3).
Byte i of a message's plaintext meets byte i of the keystream of that
message's direction, so one token's ciphertext can be addressed without
decrypting the rest, and a later packet of the same message continues where
the previous one stopped. The counter block starts with the direction, so a
request and its response never share keystream. CTR authenticates nothing:
the ciphertext's integrity is the certificate's job, and what it decrypts to
is checked by the message format.
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MASTER_SIZE = 32
KEY_SIZE = 32
BLOCK = 16
_INFO = b"interlock pair key v1"


def derive_key(master: bytes, counter: int) -> bytes:
    if len(master) != MASTER_SIZE:
        raise ValueError("master secret must be 32 bytes")
    if counter < 0:
        raise ValueError("counter must be non-negative")
    return hmac.new(master, _INFO + counter.to_bytes(8, "big"), hashlib.sha256).digest()


def commit(key: bytes) -> bytes:
    """KEY_COMMIT: the hash of the key material."""
    return hashlib.sha256(key).digest()


def crypt(key: bytes, direction: int, offset: int, data: bytes) -> bytes:
    """Encrypt or decrypt `data` sitting at byte `offset` of a message in
    `direction` (the operation is its own inverse)."""
    if len(key) != KEY_SIZE:
        raise ValueError("key must be 32 bytes")
    if offset < 0:
        raise ValueError("offset must be non-negative")
    block, skip = divmod(offset, BLOCK)
    counter = direction.to_bytes(2, "big") + bytes(6) + block.to_bytes(8, "big")
    enc = Cipher(algorithms.AES(key), modes.CTR(counter)).encryptor()
    return (enc.update(bytes(skip) + data) + enc.finalize())[skip:]


@dataclass(frozen=True)
class Pair:
    """One request/response pair's key."""
    counter: int
    key: bytes

    @property
    def commit(self) -> bytes:
        return commit(self.key)


class PairKeys:
    """The key table of one node.

    The frontend calls `new_pair()` per request. Compute calls `lookup()`
    with the KEY_COMMIT from a request header; it derives keys up to
    `lookahead` counters beyond the highest one matched so far, so requests
    may arrive out of order and a request lost on the way leaves a gap that
    costs nothing.
    """

    def __init__(self, master: bytes, next_counter: int = 0, lookahead: int = 256):
        if len(master) != MASTER_SIZE:
            raise ValueError("master secret must be 32 bytes")
        self._master = master
        self._next = next_counter
        self._lookahead = lookahead
        self._by_commit: dict[bytes, Pair] = {}
        self._derived_to = next_counter          # counters in [low, _derived_to) are in the table
        self._low = next_counter

    def new_pair(self) -> Pair:
        pair = Pair(self._next, derive_key(self._master, self._next))
        self._next += 1
        return pair

    def pair(self, counter: int) -> Pair:
        return Pair(counter, derive_key(self._master, counter))

    def lookup(self, key_commit: bytes) -> Pair | None:
        self._extend(self._low + self._lookahead)
        pair = self._by_commit.get(key_commit)
        if pair is None:
            return None
        if pair.counter >= self._low:
            self._low = pair.counter + 1            # nothing older than the newest match stays in view
            self._extend(self._low + self._lookahead)
            self._prune()
        return pair

    def _extend(self, upto: int) -> None:
        for c in range(self._derived_to, upto):
            self._by_commit[commit(derive_key(self._master, c))] = Pair(c, derive_key(self._master, c))
        self._derived_to = max(self._derived_to, upto)

    def _prune(self) -> None:
        floor = self._low - self._lookahead
        for k in [k for k, p in self._by_commit.items() if p.counter < floor]:
            del self._by_commit[k]
