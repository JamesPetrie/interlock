"""AES-128-GCM for the interlock's port-0 payloads, plus the key pre-commitment.

Runs on BOTH ends (Pi client and Spark server), so it is pure Python with no
third-party dependency: the Pi has no `cryptography` wheel and adding one to the
real-time side is not worth it for a few hundred bytes per request.

WHY THE CIPHER COMES FROM THE PROVER'S REFERENCE RECORDER
---------------------------------------------------------
The block cipher and the CTR/GCM counter layout are imported from
`VerInf/prover/ref/token_recorder.py` -- the same module the ZK circuit's AES
gadget is composed against (`prover/ref/aes_trace.py` traces it, and
`token_binding.aes_ctr_gadget` proves it). If the wire used a *different* AES
than the circuit, the proof would attest to bytes that never crossed the cable
and nobody would notice until a tamper test. One implementation, gated in
`tests/test_token_recorder.py` against FIPS-197 Appendix C and against the
`cryptography` library's AES-GCM, is the whole point.

WHAT IS PROVEN AND WHAT IS NOT
------------------------------
GCM = AES-CTR for confidentiality + GHASH for the authentication tag. The ZK
proof covers the CTR half only: it shows the committed token ids encrypt, under
a pre-committed key, to exactly the ciphertext the interlock certified. The
16-byte GCM tag is verified here, out of circuit, by the receiving end -- there
is no GHASH gadget (token-binding.md 9.3 defers it, and the interlock's own
certificate chain already provides wire integrity). So:

    tag  -> authenticity of the packet, checked by the peer at receive time
    proof-> the certified ciphertext really is these tokens under the committed key

The tag rides in the crypto header, OUTSIDE the ciphertext, so the bytes the
proof pins (`ct`) are exactly the bytes GCM produced -- token-binding.md 9.3's
preferred branch ("PAYLOAD = ciphertext only"). A SHA-256 preimage cannot be
partially explained, so mixing the tag into the proven region would force the
tag bytes to become unconstrained witness.

KEY DERIVATION AND THE PRE-COMMITMENT
-------------------------------------
    keymat(40) = HKDF-SHA256(ikm=PSK, salt=nonce, info="interlock-tokenbind-v1")
    key = keymat[0:16]   iv_in = keymat[16:28]   iv_out = keymat[28:40]
    KEY_COMMIT = Poseidon(keymat)                    # == the circuit's H2

The PSK is provisioned out of band on both boxes; only the 16-byte `nonce`
crosses the wire. `KEY_COMMIT` is sent in the REQUEST header, so the interlock
certifies it (INWARD) *before the response exists* -- which is what stops a
prover from choosing a key afterwards to make a covert payload decrypt nicely.
The key itself never crosses the cable in any form.

Distinct per-direction IVs are mandatory, not hygiene: one key covers both
streams, and a shared (key, IV) under CTR would reuse keystream and leak the
XOR of the request and response token streams. HKDF makes them distinct with
overwhelming probability; `derive()` asserts it anyway.
"""
# PORT NUMBERING (2026-08-20): the gateware binds the CLIENT role to CORETSE_1
# (RJ45 J30 / port 1) and the COMPUTE role to CORETSE_0 (J15 / port 0). Nothing
# in this file keys off the port number -- roles are carried by the forced MACs
# 02:00:00:00:00:01 (client) and ...:02 (server) -- so the swap changed cabling
# and comments only. Pi -> J30, Spark -> J15.

import hashlib
import hmac
import os
import struct
import sys

# The cipher + counter layout the circuit is composed against.
# The reference cipher lives in VerInf. Candidates, in order: an explicit
# VERINF; a sibling checkout (the Spark layout); ~/fpe/ref (the Pi, which
# gets a copy rather than the whole repo).
_here = os.path.dirname(os.path.abspath(__file__))
_verinf = os.environ.get("VERINF") or os.path.join(
    os.path.dirname(os.path.dirname(_here)), "VerInf")
for _p in (os.path.join(_here, "ref"),
           os.path.join(_verinf, "prover", "ref"),
           os.path.expanduser("~/fpe/ref")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)
import token_recorder as _tr  # noqa: E402
import poseidon_gl as _pg  # noqa: E402

KEY_BYTES, IV_BYTES = _tr.KEY_BYTES, _tr.IV_BYTES     # 16, 12
TOKEN_BYTES = _tr.TOKEN_BYTES                          # 4
KEYMAT_BYTES = KEY_BYTES + 2 * IV_BYTES                # 40
NONCE_BYTES = 16
TAG_BYTES = 16
HKDF_INFO = b"interlock-tokenbind-v1"

PSK_FILE = os.environ.get("ILK_PSK_FILE", os.path.expanduser("~/.interlock/psk"))
PSK_BYTES = 32

# ---- crypto header (rides inside the canonical payload, after the app header) ----
# request : MAGIC|ver|flags|rsvd2|nonce(16)|key_commit(32)|tag(16)   = 72 B
# response: MAGIC|ver|flags|rsvd2|tag(16)                            = 24 B
CHDR_MAGIC = b"ILKC"
CHDR_VER = 1
FLAG_ENCRYPTED = 0x01
CHDR_REQ_LEN = 8 + NONCE_BYTES + 32 + TAG_BYTES
CHDR_RSP_LEN = 8 + TAG_BYTES


# ------------------------------------------------------------------ HKDF (RFC 5869)

def hkdf_sha256(ikm, salt, info, length):
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()          # extract
    out, block, counter = b"", b"", 1                            # expand
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        out += block
        counter += 1
    return out[:length]


def load_psk(path=None):
    """The pre-shared secret, hex in a 0600 file. Provision with `--provision`
    on one box and copy to the other; both ends must hold the same bytes."""
    path = path or PSK_FILE
    try:
        with open(path) as f:
            psk = bytes.fromhex(f.read().strip())
    except FileNotFoundError:
        raise SystemExit(
            "no interlock PSK at %s -- provision it on both boxes:\n"
            "    python3 ilk_crypto.py --provision\n"
            "    scp %s <peer>:%s" % (path, path, path))
    if len(psk) != PSK_BYTES:
        raise ValueError("PSK must be %d bytes, got %d" % (PSK_BYTES, len(psk)))
    return psk


def provision(path=None):
    path = path or PSK_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    psk = os.urandom(PSK_BYTES)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(psk.hex() + "\n")
    return path


def derive(psk, nonce):
    """(keymat, key, iv_in, iv_out) for one request/response pair."""
    if len(nonce) != NONCE_BYTES:
        raise ValueError("nonce must be %d bytes" % NONCE_BYTES)
    km = hkdf_sha256(psk, nonce, HKDF_INFO, KEYMAT_BYTES)
    key, iv_in, iv_out = km[:16], km[16:28], km[28:40]
    assert iv_in != iv_out, "derived IVs collided -- keystream reuse"
    return km, key, iv_in, iv_out


def key_commit(keymat):
    """H2: the public value the ZK proof takes as its key pre-commitment.

    Poseidon over Goldilocks, not SHA-256. The wire does not care which hash
    this is -- it is 32 bytes either way, computed identically on both ends --
    but the CIRCUIT cares enormously. Proving SHA-256 means decomposing every
    word into bits, which cost ~950 claims and 92% of the binding's prove time;
    a field-native sponge is ~46 claims because it is built from the multiply
    and add the proof system already speaks. See prover/ref/poseidon_gl.py,
    including the note that this is a demo-grade instantiation rather than a
    published parameter set.

    This value must be byte-identical on the Pi, the Spark and inside the
    proof, so all three import the same module."""
    return _pg.hash_bytes(keymat)


# --------------------------------------------------------------- GHASH / GCM tag

def _gf128_mul(x, y):
    """GF(2^128) product under GCM's bit convention (MSB-first, R = 0xE1...)."""
    R = 0xE1 << 120
    z, v = 0, y
    for i in range(128):
        if (x >> (127 - i)) & 1:
            z ^= v
        v = (v >> 1) ^ R if v & 1 else v >> 1
    return z


def _ghash(h, data):
    y = 0
    for i in range(0, len(data), 16):
        blk = data[i:i + 16].ljust(16, b"\x00")
        y = _gf128_mul(y ^ int.from_bytes(blk, "big"), h)
    return y


def gcm_tag(key, iv, ct, aad=b""):
    """The 16-byte GCM tag over `aad || ct` for a 96-bit IV."""
    rks = _tr._key_expand(key)
    h = int.from_bytes(_tr._encrypt_block(rks, b"\x00" * 16), "big")
    j0 = iv + b"\x00\x00\x00\x01"
    s = _ghash(h, aad.ljust((len(aad) + 15) // 16 * 16, b"\x00")
               + ct.ljust((len(ct) + 15) // 16 * 16, b"\x00")
               + struct.pack(">QQ", len(aad) * 8, len(ct) * 8))
    ek_j0 = int.from_bytes(_tr._encrypt_block(rks, j0), "big")
    return (s ^ ek_j0).to_bytes(16, "big")


# ------------------------------------------------------------------ seal / open

def seal_tokens(key, iv, token_ids, aad=b""):
    """token ids -> (ciphertext, tag). Serialization is the circuit's:
    fixed 4-byte little-endian units, so no token straddles an AES block."""
    pt = _tr.serialize_tokens([int(t) for t in token_ids])
    ct = _tr.aes128_ctr_gcm(key, iv, pt)
    return ct, gcm_tag(key, iv, ct, aad)


def open_tokens(key, iv, ct, tag, aad=b""):
    """(ciphertext, tag) -> token ids. Raises on a bad tag; CTR is malleable,
    so an unauthenticated decrypt would hand attacker-chosen ids to the model."""
    if not hmac.compare_digest(gcm_tag(key, iv, ct, aad), tag):
        raise ValueError("GCM tag mismatch -- payload forged, corrupted, or wrong key")
    if len(ct) % TOKEN_BYTES:
        raise ValueError("ciphertext length %d not a multiple of %d" % (len(ct), TOKEN_BYTES))
    pt = _tr.aes128_ctr_gcm(key, iv, ct)          # CTR: encrypt == decrypt
    return [int.from_bytes(pt[i:i + TOKEN_BYTES], "little")
            for i in range(0, len(pt), TOKEN_BYTES)]


# ------------------------------------------------------------- header build/parse

def build_req_header(nonce, kc, tag):
    return (CHDR_MAGIC + bytes([CHDR_VER, FLAG_ENCRYPTED, 0, 0])
            + nonce + kc + tag)


def build_rsp_header(tag):
    return CHDR_MAGIC + bytes([CHDR_VER, FLAG_ENCRYPTED, 0, 0]) + tag


def parse_req_header(buf):
    """-> (nonce, key_commit, tag, ciphertext). Raises if not an ILKC v1 request."""
    if len(buf) < CHDR_REQ_LEN or buf[:4] != CHDR_MAGIC:
        raise ValueError("not an ILKC payload (magic %r)" % buf[:4])
    if buf[4] != CHDR_VER:
        raise ValueError("ILKC version %d, expected %d" % (buf[4], CHDR_VER))
    return (buf[8:8 + NONCE_BYTES],
            buf[8 + NONCE_BYTES:8 + NONCE_BYTES + 32],
            buf[8 + NONCE_BYTES + 32:CHDR_REQ_LEN],
            buf[CHDR_REQ_LEN:])


def parse_rsp_header(buf):
    """-> (tag, ciphertext)."""
    if len(buf) < CHDR_RSP_LEN or buf[:4] != CHDR_MAGIC:
        raise ValueError("not an ILKC payload (magic %r)" % buf[:4])
    if buf[4] != CHDR_VER:
        raise ValueError("ILKC version %d, expected %d" % (buf[4], CHDR_VER))
    return buf[8:CHDR_RSP_LEN], buf[CHDR_RSP_LEN:]


def is_encrypted(buf):
    return len(buf) >= 8 and buf[:4] == CHDR_MAGIC


# ------------------------------------------------- payload seal / open (both ends)
# One implementation of the framing, imported by the Pi client (infcli.py) and the
# Spark server (model_server.py). Two copies of "assemble the header, then compute
# the tag over the right bytes" is exactly the kind of thing that silently diverges.
#
# The AAD is the crypto header up to but not including the tag, so the nonce and
# KEY_COMMIT are covered by GCM's authentication, not merely carried alongside the
# ciphertext. Swapping a nonce for another invalidates the tag twice over: directly,
# and because the nonce is what derives the key that checks it.

def _req_aad(nonce, kc):
    return CHDR_MAGIC + bytes([CHDR_VER, FLAG_ENCRYPTED, 0, 0]) + nonce + kc


def _rsp_aad():
    return CHDR_MAGIC + bytes([CHDR_VER, FLAG_ENCRYPTED, 0, 0])


def seal_request(psk, token_ids, nonce=None):
    """Client side. -> (payload_bytes, ctx) where ctx carries the material the
    proof needs. `payload_bytes` goes straight into the canonical payload after
    the app header, and is what the interlock certifies INWARD."""
    nonce = nonce or os.urandom(NONCE_BYTES)
    km, key, iv_in, iv_out = derive(psk, nonce)
    kc = key_commit(km)
    aad = _req_aad(nonce, kc)
    ct = _tr.aes128_ctr_gcm(key, iv_in, _tr.serialize_tokens([int(t) for t in token_ids]))
    payload = aad + gcm_tag(key, iv_in, ct, aad) + ct
    return payload, {"nonce": nonce, "keymat": km, "key_commit": kc,
                     "iv_in": iv_in, "iv_out": iv_out, "ct_in": ct}


def open_request(psk, payload):
    """Server side. -> (token_ids, ctx). Raises on a bad tag or a KEY_COMMIT that
    does not match the one this PSK derives -- the latter means the two boxes are
    holding different secrets, which is worth naming rather than failing as
    'tag mismatch' three lines later."""
    nonce, kc, tag, ct = parse_req_header(payload)
    km, key, iv_in, iv_out = derive(psk, nonce)
    if key_commit(km) != kc:
        raise ValueError(
            "KEY_COMMIT in the request does not match this PSK -- the two ends are "
            "provisioned with different secrets (re-copy ~/.interlock/psk)")
    ids = open_tokens(key, iv_in, ct, tag, aad=_req_aad(nonce, kc))
    return ids, {"nonce": nonce, "keymat": km, "key_commit": kc,
                 "iv_in": iv_in, "iv_out": iv_out, "ct_in": ct}


def seal_response(keymat, token_ids):
    """Server side. The response rides under the SAME key as the request and the
    OTHER IV -- one key per request/response pair, distinct keystreams."""
    key, iv_out = keymat[:KEY_BYTES], keymat[KEY_BYTES + IV_BYTES:KEYMAT_BYTES]
    aad = _rsp_aad()
    ct = _tr.aes128_ctr_gcm(key, iv_out, _tr.serialize_tokens([int(t) for t in token_ids]))
    return aad + gcm_tag(key, iv_out, ct, aad) + ct, ct


def open_response(keymat, payload):
    """Client side. -> (token_ids, ct_out)."""
    key, iv_out = keymat[:KEY_BYTES], keymat[KEY_BYTES + IV_BYTES:KEYMAT_BYTES]
    tag, ct = parse_rsp_header(payload)
    return open_tokens(key, iv_out, ct, tag, aad=_rsp_aad()), ct


if __name__ == "__main__":
    if "--provision" in sys.argv:
        print("wrote %s" % provision())
    else:
        print(__doc__.strip().splitlines()[0])
