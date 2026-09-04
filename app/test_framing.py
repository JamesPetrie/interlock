"""Cross-box framing gate: what the Pi seals, the Spark opens, and back."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ilk_crypto as ic


def main():
    psk = ic.load_psk()
    req = [1, 4013, 29871, 3186, 13, 7]
    rsp = [3681, 338, 297, 3444, 29889, 2, 99, 100]

    payload, ctx = ic.seal_request(psk, req)
    assert len(payload) == ic.CHDR_REQ_LEN + 4 * len(req), "request payload size"
    ids, sctx = ic.open_request(psk, payload)
    assert ids == req, "request roundtrip: %s != %s" % (ids, req)
    assert sctx["keymat"] == ctx["keymat"], "server derived a different key"
    assert sctx["ct_in"] == ctx["ct_in"], "ciphertext differs across ends"
    print("  PASS request seal/open  (%dB payload, ct=%dB)" % (len(payload), len(ctx["ct_in"])))

    rpayload, ct_out = ic.seal_response(sctx["keymat"], rsp)
    ids2, ct_out2 = ic.open_response(ctx["keymat"], rpayload)
    assert ids2 == rsp, "response roundtrip"
    assert ct_out == ct_out2
    print("  PASS response seal/open (%dB payload, ct=%dB)" % (len(rpayload), len(ct_out)))

    # the two directions must not share keystream
    assert ctx["iv_in"] != ctx["iv_out"], "IV reuse"
    a, _ = ic.seal_tokens(ctx["keymat"][:16], ctx["iv_in"], [0, 0, 0])
    b, _ = ic.seal_tokens(ctx["keymat"][:16], ctx["iv_out"], [0, 0, 0])
    assert a != b, "same keystream in both directions"
    print("  PASS direction keystream separation")

    # tamper: flip a ciphertext byte, flip the nonce, flip KEY_COMMIT
    for name, mut in (
            ("ciphertext byte", lambda p: p[:-3] + bytes([p[-3] ^ 1]) + p[-2:]),
            ("nonce byte", lambda p: p[:8] + bytes([p[8] ^ 1]) + p[9:]),
            ("KEY_COMMIT byte", lambda p: p[:24] + bytes([p[24] ^ 1]) + p[25:])):
        try:
            ic.open_request(psk, mut(payload))
            raise AssertionError("tampered %s accepted" % name)
        except ValueError:
            pass
    print("  PASS tampered ciphertext / nonce / KEY_COMMIT all rejected")

    # a peer holding a different PSK must be named clearly, not fail obscurely
    try:
        ic.open_request(bytes(32), payload)
        raise AssertionError("wrong PSK accepted")
    except ValueError as e:
        assert "different secrets" in str(e), "unclear PSK error: %s" % e
    print("  PASS wrong PSK reports a provisioning error")
    print("=== framing: 5/5 PASS ===")


if __name__ == "__main__":
    main()
