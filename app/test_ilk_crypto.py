"""Gates for the interlock's wire crypto (app/ilk_crypto.py).

The load-bearing test is `test_gcm_matches_library`: our pure-Python AES-GCM
must be byte-identical -- ciphertext AND tag -- to a real AES-GCM
implementation. Everything else in the chain assumes that, including the ZK
circuit, which proves the CTR half against the same reference cipher.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ilk_crypto as ic


def _rand(n, seed):
    import hashlib
    out = b""
    i = 0
    while len(out) < n:
        out += hashlib.sha256(seed + bytes([i])).digest()
        i += 1
    return out[:n]


def test_gcm_matches_library():
    """Ciphertext and tag identical to `cryptography`'s AESGCM, over many sizes."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    for n_tok in (1, 2, 4, 7, 8, 16, 24, 33, 64):
        key = _rand(16, b"k%d" % n_tok)
        iv = _rand(12, b"i%d" % n_tok)
        toks = [(i * 7919 + 13) % 32000 for i in range(n_tok)]
        ct, tag = ic.seal_tokens(key, iv, toks)
        want = AESGCM(key).encrypt(iv, ic._tr.serialize_tokens(toks), None)
        assert ct + tag == want, (
            "n_tok=%d: ours %s != library %s" % (n_tok, (ct + tag).hex(), want.hex()))
    print("    GCM ct+tag identical to library over 9 sizes")


def test_gcm_with_aad_matches_library():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key, iv = _rand(16, b"ka"), _rand(12, b"ia")
    aad = b"REQ\x00\x00\x00\x00\x07" + b"\x00" * 8
    toks = list(range(20))
    ct, tag = ic.seal_tokens(key, iv, toks, aad=aad)
    want = AESGCM(key).encrypt(iv, ic._tr.serialize_tokens(toks), aad)
    assert ct + tag == want, "AAD case differs from library"
    print("    GCM with AAD identical to library")


def test_roundtrip_and_tag_rejection():
    key, iv = _rand(16, b"kr"), _rand(12, b"ir")
    toks = [1, 500, 31999, 7]
    ct, tag = ic.seal_tokens(key, iv, toks)
    assert ic.open_tokens(key, iv, ct, tag) == toks, "roundtrip lost tokens"
    # every single-bit ciphertext flip must be caught by the tag
    for bit in (0, 7, 13, 31):
        bad = bytearray(ct); bad[bit // 8] ^= 1 << (bit % 8)
        try:
            ic.open_tokens(key, iv, bytes(bad), tag)
            raise AssertionError("flipped ct bit %d accepted" % bit)
        except ValueError:
            pass
    bad_tag = bytearray(tag); bad_tag[0] ^= 1
    try:
        ic.open_tokens(key, iv, ct, bytes(bad_tag))
        raise AssertionError("flipped tag accepted")
    except ValueError:
        pass
    print("    roundtrip ok; 4 ct flips + 1 tag flip all rejected")


def test_ctr_half_is_what_the_circuit_proves():
    """The sealed ciphertext must equal the recorder's CTR output exactly --
    that is the value the AES gadget pins as `ct_public`."""
    key, iv = _rand(16, b"kc"), _rand(12, b"ic")
    toks = [11, 22, 33, 44, 55]
    ct, _ = ic.seal_tokens(key, iv, toks)
    ref = ic._tr.aes128_ctr_gcm(key, iv, ic._tr.serialize_tokens(toks))
    assert ct == ref, "wire ciphertext diverged from the circuit's reference AES"
    print("    wire ciphertext == prover/ref CTR output")


def test_derive_is_deterministic_and_separates_directions():
    psk = _rand(32, b"psk")
    nonce = _rand(16, b"n1")
    km, key, iv_in, iv_out = ic.derive(psk, nonce)
    km2, key2, _, _ = ic.derive(psk, nonce)
    assert km == km2 and key == key2, "derive not deterministic"
    assert iv_in != iv_out, "per-direction IVs collided"
    assert len(km) == 40 and km[:16] == key
    # a different nonce or a different PSK gives unrelated key material
    assert ic.derive(psk, _rand(16, b"n2"))[0] != km, "nonce does not vary keymat"
    assert ic.derive(_rand(32, b"psk2"), nonce)[0] != km, "PSK does not vary keymat"
    print("    HKDF deterministic, direction-separated, nonce+PSK sensitive")


def test_key_commit_layout_and_hash():
    """Two separate invariants, easy to conflate:

    LAYOUT -- the 40 bytes hashed must still be key||iv_in||iv_out, exactly as
    the AES reference recorder lays them out. This is what makes the bytes the
    circuit hashes the same bytes the cipher uses, and it must not drift.

    HASH -- KEY_COMMIT is Poseidon, NOT the recorder's SHA-256 `H2`. The
    recorder still computes H2 with SHA-256 because it is the AES/SHA
    reference; the key commitment moved to a field-native hash because proving
    SHA-256 cost 92% of the binding. Asserting they differ keeps the two from
    being silently re-conflated later.
    """
    import hashlib
    import sys as _s
    _s.path.insert(0, os.path.join(ic._verinf, "prover", "ref"))
    import poseidon_gl as pg

    psk, nonce = _rand(32, b"pk"), _rand(16, b"nn")
    km, key, iv_in, iv_out = ic.derive(psk, nonce)
    rec = ic._tr.record([1, 2, 3], [4, 5], key, iv_in, iv_out)
    assert rec["key_material"] == km.hex(), "keymat layout drifted from the recorder"
    assert ic.key_commit(km) == pg.hash_bytes(km), "KEY_COMMIT is not Poseidon(keymat)"
    assert len(ic.key_commit(km)) == 32, "KEY_COMMIT must stay 32 bytes on the wire"
    assert ic.key_commit(km).hex() != rec["H2"], \
        "KEY_COMMIT collided with the recorder's SHA-256 H2 -- the two are distinct"
    assert ic.key_commit(km) != hashlib.sha256(km).digest(), "still SHA-256"
    print("    keymat layout == recorder; KEY_COMMIT is Poseidon, 32 B, != SHA-256")


def test_headers_roundtrip():
    nonce, kc, tag = _rand(16, b"h1"), _rand(32, b"h2"), _rand(16, b"h3")
    ct = _rand(48, b"h4")
    buf = ic.build_req_header(nonce, kc, tag) + ct
    assert len(buf) == ic.CHDR_REQ_LEN + 48
    n2, k2, t2, c2 = ic.parse_req_header(buf)
    assert (n2, k2, t2, c2) == (nonce, kc, tag, ct), "request header roundtrip"
    buf = ic.build_rsp_header(tag) + ct
    t3, c3 = ic.parse_rsp_header(buf)
    assert (t3, c3) == (tag, ct), "response header roundtrip"
    assert ic.is_encrypted(buf) and not ic.is_encrypted(b"REQ\x00" + b"\x00" * 12)
    try:
        ic.parse_req_header(b"XXXX" + b"\x00" * 80)
        raise AssertionError("bad magic accepted")
    except ValueError:
        pass
    print("    crypto headers roundtrip; bad magic rejected")


if __name__ == "__main__":
    fails = 0
    for name in sorted(n for n in dir() if n.startswith("test_")):
        try:
            globals()[name]()
            print("  PASS %s" % name)
        except Exception as e:
            print("  FAIL %s: %s: %s" % (name, type(e).__name__, e))
            fails += 1
    print("=== ilk_crypto: %d/%d %s ===" % (7 - fails, 7, "PASS" if not fails else "FAIL"))
    raise SystemExit(fails)
