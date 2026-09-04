# Encrypted transport + in-circuit key binding

The interlock's port-0 payloads are AES-128-GCM. The ZK challenge proves, in the
same circuit as the forward pass, that a **key committed before the response
existed** opens the **certified** request and response ciphertexts to the token
streams the model was proven on.

```
  Pi                          interlock (FPGA)                     Spark
  --                          ----------------                     -----
  tokenize
  keymat = HKDF(PSK, nonce)
  ct_in  = AES-CTR(k, iv_in, ids)
  tag    = GHASH...
  [hdr | nonce | KEY_COMMIT | tag | ct_in] --> certified INWARD --> derive same
                                                                    keymat, verify
                                                                    tag, decrypt
                                                                    generate
  decrypt <-- certified OUTWARD <-------------- [hdr | tag | ct_out]
```

Nothing readable crosses the cable, and **the key never crosses it at all**: both
ends derive it from a pre-shared secret and the 16-byte nonce that rides in the
request.

## What is proven, and where

| statement | where checked |
|---|---|
| `SHA256(key‖iv_in‖iv_out) == KEY_COMMIT` | **in circuit** (B2) |
| `AES-CTR(key, iv_in,  req_tokens) == ct_in` | **in circuit** (B1_in) |
| `AES-CTR(key, iv_out, rsp_tokens) == ct_out` | **in circuit** (B1_out) |
| response tokens are the model's *own* committed output tokens | **in circuit** (weld) |
| `KEY_COMMIT`/`ct_in`/`ct_out` are the certified bytes | interlock cert + byte-audit |
| GCM tag authenticity | at receive time, out of circuit |

All the in-circuit claims live on the **same tape** as the 22-layer forward pass,
so the verifier's single ACCEPT covers them. There is no separate binding proof
to correlate.

### Why both halves are needed

`B1` alone is vacuous. For *any* key' a prover picks,
`tokens' = AES_dec(ct, key')` re-encrypts to `ct`, so they could grind key' until
tokens' said whatever they liked. `B2` pins the key to the one whose commitment
was already on the wire. With key and ciphertext both fixed,
`tokens = AES_dec(ct, key)` is uniquely the real stream.

### Why the commitment is a *pre*-commitment

`KEY_COMMIT` travels in the **request**, so the interlock's INWARD digest fixes
it a full epoch before the response exists. A prover cannot choose a key
afterwards to make a covert payload decrypt innocuously.

### Why the ciphertext is public here

`token-binding.md` §2 states B1 as `SHA256(AES(...)) == H1` because in the general
recorder setting the verifier holds only a digest. On the interlock the verifier
holds the ciphertext — it captured the frames, and `commit.audit` already proved
that capture *is* the certified traffic. Pinning the ciphertext directly is
strictly stronger (no preimage to argue about) and much cheaper: it removes a
multi-block SHA-256 over the payload, leaving only the single-block SHA-256 over
the 40-byte key material. Confidentiality is unaffected — the ciphertext is on
the cable regardless. The tokens stay hidden.

## Key material

    keymat(40) = HKDF-SHA256(ikm=PSK, salt=nonce, info="interlock-tokenbind-v1")
    key = keymat[0:16]   iv_in = keymat[16:28]   iv_out = keymat[28:40]
    KEY_COMMIT = SHA256(keymat)

The per-direction IVs **must** differ: one key covers both streams, and a shared
`(key, IV)` under CTR would reuse keystream and leak the XOR of the request and
response token streams. `derive()` asserts it.

PSK provisioning (both boxes must hold the same bytes):

    python3 ilk_crypto.py --provision            # writes ~/.interlock/psk, 0600
    scp ~/.interlock/psk 2a-rpi:~/.interlock/psk
    ssh 2a-rpi 'sudo cp ~/.interlock/psk /root/.interlock/psk'   # infcli runs as root

A mismatched PSK is reported as a provisioning error, not as a tag failure.

## Wire format

Canonical payload, after the interlock's 64-byte header and the app's 16-byte one:

    request : "ILKC" ver flags rsvd2 | nonce(16) | KEY_COMMIT(32) | tag(16) | ct
    response: "ILKC" ver flags rsvd2 | tag(16) | ct

The GCM AAD is the crypto header up to but excluding the tag, so the nonce and
KEY_COMMIT are authenticated, not merely carried alongside. Token serialization is
fixed 4-byte little-endian units, so no token straddles an AES block — the same
layout the circuit's byte decomposition uses.

## Measured (TinyLlama-1.1B-Chat, 13-token prompt, 8-token response)

| mode | wall | key binding |
|---|---|---|
| `wire` | 7.4 s | not requested |
| `fast` (subsampled) | 46 s | proven, **not welded** |
| `sound` tq=10 | 131 s | proven **and welded** |

The binding adds ~1 000 claims and roughly 13 s to the sound path. It is *not*
subsampled in fast mode — B1/B2 are proven in full there, because they are cheap;
what fast mode omits is the weld, since that path never builds the model's
output-token commitment.

## Limits — read before presenting this

1. **The prompt is not hidden from the verifier.** The forward pass binds input
   tokens through `EmbeddingLookupClaim`, whose `token_ids` are public, so the
   request-side binding pins its tokens to those same public constants. The
   *response* tokens are genuinely hidden and welded. Hiding the prompt is
   `token-binding.md` P4 (`OneHotSelectClaim`).
2. **The GCM tag is not in the circuit.** No GHASH gadget exists; the tag is
   verified by the receiving end. The proof covers the CTR half, which is what
   carries the token↔ciphertext relation. (`token-binding.md` §9.3 defers GHASH.)
3. **AES table contents are a policy obligation, not a proven fact.** The verifier
   checks the proof is consistent with whatever table data the claim list carries.
   Deployment policy must additionally pin `tb_sbox`/`tb_xtime`/`tb_xor8` to the
   real AES tables — exactly as it must pin the model structure. Inherited from
   `token-binding.md` P1; not closed here.
4. **The certificate chain is demonstrative.** The cert HMAC key is the public
   constant `99`, hardcoded in gateware and duplicated in Python. `tau` shows the
   mechanism, not unforgeability. Everything above reduces to "the interlock
   certificate chain is sound", which in this build it is not.
5. **Symmetric, and the Spark holds the PSK.** The binding stops a prover from
   proving on tokens other than the ones it shipped under a pre-committed key. It
   is not a statement about a key the prover does not know.

## Files

| file | role |
|---|---|
| `app/ilk_crypto.py` | HKDF, AES-GCM, framing. Shared verbatim by both ends. |
| `app/test_ilk_crypto.py` | 7 gates incl. byte-equality vs the `cryptography` library |
| `app/test_framing.py` | 5 gates: seal/open across the two ends, tamper rejection |
| `prover/crypto_binding.py` | B1+B2 composition and the cross-gadget wiring |
| `prover/tests/test_crypto_binding.py` | 9 gates, blinding ON, incl. key-desync + weld cheats |
| `prover/token_binding.py` | P1–P3 gadgets (tables, SHA-256, AES-CTR) |
| `prover/ref/token_recorder.py` | the reference cipher — mounted into the container, not copied |

The container mounts `prover/ref` read-only at `/app/ref` rather than taking a
copy, so the wire cipher and the cipher the circuit proves cannot drift apart.
