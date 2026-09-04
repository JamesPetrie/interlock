#!/usr/bin/env python3
"""Client-side tokenizer for the interlock Pi.

The interlock's wire payload is ALWAYS the canonical little-endian uint32 token
array -- text never crosses the link. Until now the Pi had no tokenizer, so
demo_e2e.py tokenized and detokenized on the Spark: the server's own box. That
works, but it means the "client" never actually handles the text layer, which is
not the architecture the design describes (the README's Mac client ships its own
tokenizer files precisely so the client owns text).

This is the Pi's half. It needs only `tokenizers` (Rust-backed, ~3 MB) plus a
1.8 MB tokenizer.json -- no transformers, no torch, which is why it fits here.

Verified id-for-id against the Spark's transformers AutoTokenizer, including the
BOS that add_special_tokens=True prepends:
    [1, 894, 29901, 1724, 338, 278, 7483, 310, 3444, 29973, 13, 22550, 29901]

Usage:
    echo -n "Question: ...\\nAnswer:" | tok.py encode     -> canonical hex payload
    tok.py decode <hex>                                   -> text
    tok.py ids <hex>                                      -> comma-separated ids
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.environ.get("TOKENIZER_DIR", os.path.join(HERE, "tokenizer/tinyllama"))

from tokenizers import Tokenizer


_CACHE = {"t": None}


def tokenizer():
    """The tokenizer, loaded once. The CLI paths below run one command per
    process so they never noticed the reload, but pi_agent holds this open for
    the life of the demo and a 1.8 MB re-parse per prompt is not free."""
    if _CACHE["t"] is None:
        _CACHE["t"] = Tokenizer.from_file(os.path.join(MODEL, "tokenizer.json"))
    return _CACHE["t"]


def _tok():
    return tokenizer()


def encode_ids(text):
    """Token ids for `text`. The importable form -- `encode` below wraps it in
    the hex framing the CLI and the wire use."""
    return list(tokenizer().encode(text).ids)


def decode_ids(ids):
    return tokenizer().decode(list(ids))


def encode(text):
    ids = encode_ids(text)
    return b"".join(struct.pack("<I", i & 0xFFFFFFFF) for i in ids).hex()


def _ids_from_hex(h):
    b = bytes.fromhex(h.strip())
    return [struct.unpack_from("<I", b, i)[0] for i in range(0, len(b), 4)]


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    cmd = sys.argv[1]
    if cmd == "encode":
        # via stdin: prompts contain newlines, which argv mangles over ssh
        sys.stdout.write(encode(sys.stdin.read()))
        return 0
    if cmd in ("decode", "ids"):
        if len(sys.argv) < 3:
            print("need hex", file=sys.stderr)
            return 2
        ids = _ids_from_hex(sys.argv[2])
        if cmd == "ids":
            sys.stdout.write(",".join(str(i) for i in ids))
        else:
            # Never let the BPE cleanup pass run: it strips spaces before
            # punctuation and would not round-trip the model's own output.
            sys.stdout.write(_tok().decode(ids))
        return 0
    print("unknown command %r" % cmd, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
