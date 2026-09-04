# Interlock app — the deployment against real silicon

The runnable application layer: a prompt goes from a Raspberry Pi, across an
MPF300 FPGA that certifies every byte in both directions, to a DGX Spark that
answers it and proves the answer follows from the question. Encrypted end to
end, with the proof generated **and verified** on the Spark — only small control
messages cross the wire.

Spec: [`../docs/inference-cli-app.md`](../docs/inference-cli-app.md).
Encryption and the key binding: [`ENCRYPTION.md`](ENCRYPTION.md).

This is the hardware sibling of [`../prototype/`](../prototype), which models the
same certificate/challenge dataflow in pure Python with no FPGA and no proof.

```
 Pi (client)                  MPF300 (bump-in-the-wire)                DGX Spark
 tokenize, seal    ── eth0 →│ port 0 · J15      port 1 · J30 │← enP7s7 ──  decrypt,
 open, verify      ←────────│  certifies both directions     │────────→    infer, prove
```

## Files

**The demo path** — these nine are what a run touches, and nothing else is reachable from them.

| file | role |
|---|---|
| `run_demo.sh` | one command, three modes (`wire` / `fast` / `sound`), prints a timing table |
| `bringup.sh` | brings up the GPU backend and the wire half after a reboot or power cycle |
| `demo_e2e.py` | the six-stage driver: preflight → tokenize → send → decode → challenge → verdict |
| `model_backend.py` | GPU half (host venv): greedy decode, and the resident prover worker |
| `model_server.py` | wire half (container): raw L2, decrypt, GCM-authenticate, in-band ZK router |
| `infcli.py` | the Pi's client: seal, send, capture certs, open the response, print the panel |
| `canon_tx.py` | the bucket-timed canonical port — the flywheel that locks to the board's 1 kHz sync |
| `commit.py` | recomputes the certificate's digest hierarchy from retained traffic (the byte-audit) |
| `ilk_crypto.py` | AES-128-GCM, HKDF key derivation, the `KEY_COMMIT` pre-commitment |

**Client half, deployed to the Pi**

| file | role |
|---|---|
| `tok.py` | the Pi's tokenizer — text never crosses the wire, so the client owns the text layer |
| `sync_pi.sh` | push the client half to the Pi; `--check` reports drift instead of changing anything |

**Diagnostics** — not on the demo path, but what you reach for when the board misbehaves:
`cert_parse.py`, `cert_send_spaced.py`, `dump_rx.py`, `size_probe.py`. And
`rehearse.py`, for when the board behaves but the *verdicts* might not: it drives
the web demo's own socket through one honest and one tampered run and demands
PASS then FAIL — `postboot-check.sh` proves the services are up, this proves the
demo is right. Two real proofs (~7 min); run it before an audience does.

**Tests**: `test_ilk_crypto.py` (AES-GCM against the `cryptography` library),
`test_framing.py` (seal/open across both ends), `test_commit.py`, `test_frames.py`.

The proof lives in the **VerInf** repo, expected as a sibling checkout (`VERINF`
overrides). `model_backend.py` runs `VerInf/analysis/interlock_challenge.py` —
or `subsample_challenge.py` in fast mode — which proves the
unexplained-information bound, checks it with the standalone Rust verifier, and
binds the key.

## What crosses the wire

**Not text, and no longer token ids — ciphertext.** The payload is AES-128-GCM
sealed at the endpoints; the interlock has no key and needs none, because its job
is to commit the exact bytes that crossed, in the bucket they declared.

```
canonical header (64) │ app header (16) │ crypto header │ ciphertext
                                          request  72 B: "ILKC" | nonce(16) | KEY_COMMIT(32) | tag(16)
                                          response 24 B: "ILKC" | tag(16)
```

The per-request key is **derived, never transmitted**: `HKDF(PSK, nonce)` on both
ends, with distinct IVs per direction so one key cannot reuse keystream across
the two streams. `KEY_COMMIT` rides in the *request*, so the interlock certifies
it before the response exists — which is what makes it a *pre*-commitment the
proof can bind to.

## The binding, end to end

Four independent checks over the same bytes:

1. the **certificate** commits the request and response payloads (gateware HMAC `tau`);
2. the **byte-audit** recomputes that certificate's digest from retained traffic,
   proving the retained set *is* that period's traffic, not merely consistent with it;
3. the **model** produces the response from the request under greedy decode;
4. the **proof** shows a pre-committed key opens the certified ciphertexts to the
   proven tokens — `Poseidon(key‖iv_in‖iv_out) == KEY_COMMIT`, and AES-CTR maps
   those tokens to exactly the certified `ct_in` / `ct_out`.

In `sound` mode the response tokens are additionally **welded** to the model's own
committed output tokens, so the decrypted stream is the stream that was scored.
In `fast` mode it is not, and the panel says `n/a (spot-check mode)` rather than OK.

## Run it

```bash
./bringup.sh                 # backend + wire half; needs the board emitting sync
./run_demo.sh                # fast (default): ~24 s
./run_demo.sh sound 10       # full four-round proof: ~115 s
./run_demo.sh wire           # certified round trip, no proof: ~8 s
./sync_pi.sh --check         # confirm the Pi matches this checkout
```

`fast` is a **spot check, not a proof**: one (token, layer) of ~460 is proven, so
a cheating prover passes ~99.8% of the time. It shows the protocol's shape and
produces the real U. Never present its verdict as a proof — `run_demo.sh` labels
it from what the prover actually reported, not from the mode you asked for.

## Hard rule

One packet in flight at a time, spaced (default 300 ms). The per-packet cert HMAC
has no back-pressure; a flood wedges the interlock. Client and server are both
single-in-flight by construction, and the server spaces its control replies.

## Operational — the board goes quiet

The interlock stops emitting sync roughly **4 hours** after each power-up, with
both PHY links still showing carrier and nothing logged. Measured at 3 h 59 m 53 s
by `../bench/uptime_watch.py` — that precision is a timer expiring, not a fault
accumulating.

**Recovery: reboot the Spark.** Confirmed 2026-08-20. Reflashing alone does *not*
revive it, and neither does bouncing the NIC, `ethtool -r`, a 35-second link-down,
or resetting the programmer's USB — all tested and all failed. A reboot resets the
NIC at the PCIe level, which is the likely mechanism; a `pci remove` + `rescan`
without rebooting is the obvious next thing to try and has not been tested.

## Deployment

The stack is split because the two halves need incompatible privileges: the wire
half runs in a slim container (`--network host`, `NET_RAW`, `NET_ADMIN`, `SYS_NICE`
for raw L2 and RT scheduling), while the GPU half runs on the host's validated
CUDA venv. They talk over loopback. Keeping the multi-minute proof off the
real-time thread is good hygiene besides — it must never stall the flywheel.
