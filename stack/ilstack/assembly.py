"""Reassembling messages from received packets, and decrypting them.

A message's packets are linked by reference: the first references something
outside the message, each later one its predecessor. Where a message ends
only its key holder knows, from the total length in the encrypted prefix of
its first payload, so this stage sits with the keys: the first packet of a
message is decrypted to learn the key (through a callback: by KEY_COMMIT on
compute, by the referenced request on the frontend) and the length, each
later packet continues the keystream where the previous one stopped, and
the message is complete when the length is reached.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import crypto, messages, packet as P


@dataclass
class Message:
    body: bytes                 # the plaintext
    key: bytes
    key_commit: bytes
    first: P.Id
    last: P.Id
    reference: P.Id             # what the first packet references


@dataclass
class _Partial:
    key: bytes
    key_commit: bytes
    first: P.Id
    reference: P.Id
    body: bytearray
    total: int | None          # unknown until the prefix is in


@dataclass
class AssemblyStats:
    messages: int = 0
    unknown_key: int = 0
    malformed: int = 0
    expired: int = 0


class Assembler:
    def __init__(self, direction: int, key_for: Callable[[P.Traffic], bytes | None], max_open: int = 1024):
        """`direction` is the direction of the packets fed in; `key_for`
        gives the key of a message from its first packet, or None."""
        self.direction = direction
        self.key_for = key_for
        self.max_open = max_open
        self.stats = AssemblyStats()
        self._open: dict[P.Id, _Partial] = {}         # last packet ID -> the message it continues

    def feed(self, pkt: P.Traffic) -> Message | None:
        """A complete message, if this packet finished one."""
        partial = None
        if pkt.reference.dir == self.direction and pkt.reference != pkt.id:
            partial = self._open.pop(pkt.reference, None)
        if partial is None:
            key = self.key_for(pkt)
            if key is None:
                self.stats.unknown_key += 1
                return None
            partial = _Partial(key, pkt.key_commit, pkt.id, pkt.reference, bytearray(), None)
        partial.body += crypto.crypt(partial.key, self.direction, len(partial.body), pkt.payload)
        if partial.total is None and len(partial.body) >= messages.PREFIX_SIZE:
            try:
                partial.total = messages.total_length(bytes(partial.body[:messages.PREFIX_SIZE]))
            except (ValueError, Exception):
                self.stats.malformed += 1
                return None
        if partial.total is not None and len(partial.body) > partial.total:
            self.stats.malformed += 1
            return None
        if partial.total is not None and len(partial.body) == partial.total:
            self.stats.messages += 1
            return Message(bytes(partial.body), partial.key, partial.key_commit, partial.first, pkt.id, partial.reference)
        self._open[pkt.id] = partial
        if len(self._open) > self.max_open:                      # the oldest unfinished message gives way
            self._open.pop(next(iter(self._open)))
            self.stats.expired += 1
        return None


def encrypt(key: bytes, direction: int, body: bytes, s_max: int) -> list[bytes]:
    """A message body as encrypted payloads, ready for the dispatcher."""
    return [crypto.crypt(key, direction, off, chunk)
            for off, chunk in _offsets(messages.split(body, s_max))]


def _offsets(chunks: list[bytes]):
    off = 0
    for c in chunks:
        yield off, c
        off += len(c)
