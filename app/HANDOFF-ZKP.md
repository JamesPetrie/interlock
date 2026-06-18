# Handoff — completing the prototype (ZKP agent)

Everything except the ZKP itself is built and tested on silicon. You implement **two**
things; the rest (interlock wire I/O, in-band control channel, framing, spacing,
certificate verify + traffic binding, plaintext extraction, packet retention) is done.

1. The marked block in **`model_server.py : handle_challenge()`** — chain steps (e),(f),(g)
   and the RESULT message.
2. With the model agent, **`generate()`** — a real model instead of the echo stub.

Read alongside `../docs/inference-cli-app.md`: §6 (the verification chain), §6.3 (your
ZKP interface — already resolved to infproof/Ligero), §9 (in-band channel + reference
impl).

---

## What already works (verified)

| piece | status |
|---|---|
| In-band channel: challenge + STATUS + RESULT over the interlock (no WiFi/SSH) | **loopback-verified** |
| Per-packet certs: parse + `tau` (HMAC) + `overall` (hash) | **6/6 on silicon** |
| `handle_challenge` plumbing: (a) `tau`, (b) bind cert→retained packet, (d) extract plaintext | **verified** (happy + FAIL(a) tamper + FAIL(b) not-retained) |
| Packet retention (req+rsp kept by `overall` hash for binding) | done |
| The proof never crosses the wire (verify-on-Spark) | by design |

So when your code runs, the certs are already proven authentic and bound to the actual
certified traffic, and you have the cert-verified plaintext in hand.

---

## The interface you implement

`handle_challenge(send, header, body, store, key)` in `model_server.py`, called when a
CHALLENGE control packet arrives.

- **`send(mtype, data: bytes)`** — emit one spaced control reply back through the
  interlock to the MacBook. Use `T_STATUS` for progress, `T_RESULT` for the verdict.
  Each `send` is one certified packet and is auto-spaced (`CTL_GAP`); keep STATUS sparse
  (seconds apart, which the minutes-long proof gives you for free) — **don't loop fast**.
- **By the time you reach the marked block**, steps (a)+(b) have passed (else the
  function already `send`-FAILed and returned) and you have, cert-verified and
  traffic-bound:
  - `req_tokens` — the challenged **request** payload (plaintext token-id bytes),
  - `rsp_tokens` — the paired **response** payload.

Your block (chain steps e,f,g — see spec §6.2 / §6.3):

```
1. prove on (req_tokens, rsp_tokens) -> proof          # minutes; send(T_STATUS, b"proving 40%...")
2. verify_proof(proof)               -> ok, U          # verifier-rs/target/release/verify_proof
3. extract transcript (input ids + output fingerprint) # §6.3 transcript extractor
4. (e) req_tokens == transcript.input_ids
       output fingerprint  == fp over rsp_tokens
   (g) those public values bind the proof to the certified bytes (transitively, via the
       app — §6.3; no in-circuit hashing in the prototype)
5. send(T_RESULT, <verdict>)
```

---

## RESULT content (the user's requirement — spec §6.1)

The user must see, for the challenged packet: **(1) its plaintext, (2) the ZKP
transcript plaintext, (3) a match check**, plus PASS/FAIL and `U`. Put these in the
RESULT; chunk across spaced `send()`s if it exceeds one ~1400-byte frame. Skeleton:

```
PASS   U=0.041
prompt     : <decode(req_tokens)>
completion : <decode(rsp_tokens)>
zkp.input  : <transcript input>     [match: OK]
zkp.output : fingerprint            [match: OK]
```

`FAIL` cases should name the failing step, e.g. `FAIL (e): completion != proof output`
vs `FAIL (f): proof invalid` — distinct findings.

---

## Token serialization — pin this, it IS the binding

The payload (DATA after the 16-byte header) **is** the token-id array in a fixed
encoding (e.g. little-endian `uint32`, in order). `req_tokens` / `rsp_tokens` are those
raw bytes. Decode them the *same way* your prover/transcript does so step (e) is a real
id-for-id comparison. Pin this encoding in both the sender (`infcli.py`) and your prover;
it is what ties the certified bytes to the proof's public tokens.

(Prototype = plaintext payloads, so chain steps (c) `H(key)` and (d) decrypt collapse to
"read the token ids." Encryption later only widens the header for `recomputation_commitment`; your ZKP side is unaffected — §6.3.)

---

## `generate()` (model agent — noted for completeness)

Replace the echo: `decrypt → request token-ids → HF generate → response token-ids →
encrypt`, copying the request ID into `rsp_header`. The wire I/O around it is fixed, and
the model server already retains every processed request+response (the `remember()`
calls) keyed by `overall` hash — keep that, it's what challenge binding (b) uses.

---

## Run / test

Spark (port 1) — promiscuous mode is required (`CAP_NET_ADMIN`):
```
docker run --rm --network host --cap-add NET_RAW --cap-add NET_ADMIN -v ~/fpe:/fpe \
  python:3-slim python3 /fpe/model_server.py enP7s7
```
Drive it:
- bring-up (loopback, no Mac): `python3 zk_challenge_send.py enxb8fbb3b1f53c <rid>` sends a
  dummy CHALLENGE → exercises routing + the graceful "no certs" reply.
- real: from the Mac, `sudo python3 infcli.py --iface en7 send --text "..."` then
  `infcli.py challenge <rid>` (ships `request_id || req_cert || rsp_cert`).
- watch replies on port 0 (`enxb8`): `tcpdump -A -i enxb8fbb3b1f53c 'ether src 02:00:00:00:00:02'`
  and look for the `ILKZKCTL` marker.

The loopback rig (model_server on `enP7s7` + sends into `enxb8`) reproduces both the
request→cert loop and the challenge→status→result loop without the MacBook.

---

## Don't

- **Don't burst control replies** — each is a certificate; the per-packet HMAC has no
  back-pressure and a fast burst wedges the interlock (power-cycle to recover). The
  `send` helper spaces them; just don't drive it in a tight loop.
- **Don't move the proof over the wire** — verify on the Spark, send only the result.
- **Don't bind by `request_id`** — binding is by `overall`-hash match (done in (b));
  `request_id` is just a human label.

## Files

`model_server.py` (your `handle_challenge` + `generate`), `infcli.py` (Mac driver),
`zk_challenge_send.py` (CHALLENGE sender), `cert_parse.py` / `cert_send_spaced.py` (cert
decoder/verifier + spaced sender). Spec: `../docs/inference-cli-app.md`.
