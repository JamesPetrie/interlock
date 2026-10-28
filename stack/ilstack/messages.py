"""What the encrypted payloads carry, in whole token units.

A message is a sequence of 4-byte units: a prefix of four units (format
version, kind, meta length in bytes, token count), the meta bytes (JSON,
zero-padded to a whole unit), then one unit per token id. A receiver reads
the prefix from the first packet to learn the message's total length and so
to know when the message is complete: completion markers live inside the
encrypted payload, as the protocol requires, never in a header.

A request carries the prompt's token ids and the generation parameters; a
response the generated token ids and how generation ended.
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field

UNIT = 4
PREFIX = struct.Struct("!IIII")
PREFIX_SIZE = PREFIX.size
FORMAT_VERSION = 1
KIND_REQUEST = 1
KIND_RESPONSE = 2
MAX_TOKEN = (1 << 32) - 1


@dataclass
class Request:
    tokens: list[int]
    params: dict = field(default_factory=dict)      # max_tokens, temperature, top_p, seed, stop


@dataclass
class Response:
    tokens: list[int]
    finish_reason: str = "stop"                     # "stop" | "length" | "error"
    error: str | None = None


def encode(msg: Request | Response) -> bytes:
    if isinstance(msg, Request):
        kind, meta = KIND_REQUEST, dict(msg.params)
    elif isinstance(msg, Response):
        kind, meta = KIND_RESPONSE, {"finish_reason": msg.finish_reason}
        if msg.error is not None:
            meta["error"] = msg.error
    else:
        raise TypeError("not a message")
    m = json.dumps(meta, separators=(",", ":")).encode()
    m += bytes(-len(m) % UNIT)
    if any(not 0 <= t <= MAX_TOKEN for t in msg.tokens):
        raise ValueError("token id out of range")
    return PREFIX.pack(FORMAT_VERSION, kind, len(m), len(msg.tokens)) + m + b"".join(
        t.to_bytes(UNIT, "big") for t in msg.tokens)


def total_length(prefix: bytes) -> int:
    """The whole message's length in bytes, from its first PREFIX_SIZE bytes."""
    version, kind, meta_len, n = PREFIX.unpack_from(prefix)
    if version != FORMAT_VERSION:
        raise ValueError(f"unsupported message format {version}")
    if kind not in (KIND_REQUEST, KIND_RESPONSE) or meta_len % UNIT:
        raise ValueError("malformed message prefix")
    return PREFIX_SIZE + meta_len + n * UNIT


def decode(body: bytes) -> Request | Response:
    if len(body) < PREFIX_SIZE or len(body) != total_length(body[:PREFIX_SIZE]):
        raise ValueError("message length mismatch")
    _, kind, meta_len, n = PREFIX.unpack_from(body)
    raw = body[PREFIX_SIZE:PREFIX_SIZE + meta_len].rstrip(b"\0")
    meta = json.loads(raw.decode()) if raw else {}
    start = PREFIX_SIZE + meta_len
    tokens = [int.from_bytes(body[start + i * UNIT:start + (i + 1) * UNIT], "big") for i in range(n)]
    if kind == KIND_REQUEST:
        return Request(tokens, meta)
    return Response(tokens, meta.get("finish_reason", "stop"), meta.get("error"))


def split(body: bytes, s_max: int) -> list[bytes]:
    """A message body as packet payloads: `s_max` bytes each (a whole number
    of units), the last one shorter. A body is at least one payload."""
    size = s_max - s_max % UNIT
    if size <= 0:
        raise ValueError("s_max smaller than a token unit")
    return [body[i:i + size] for i in range(0, len(body), size)] or [b""]
