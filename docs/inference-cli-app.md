# Inference CLI app over the interlock, with ZK challenge

**Audience.** The agent who owns the ZKP repo (`zkllm-entropy` / `ligero` / `infproof`).
This is a build spec for a command-line app that runs on a **MacBook acting as the
prover frontend**, drives inference request/response packets through the interlock,
captures and verifies the per-packet certificates, and — on demand — **challenges a
packet**: reveals its plaintext, obtains a zero-knowledge proof of that packet's
computation, and checks that the plaintext, the certificate, and the proof transcript
all agree.

Everything in §2–§4 is **ground truth verified on silicon** (MPF300, 2026-06-18);
§6–§7 is where you design the ZKP side. Where something is an open design choice it is
marked **[ZKP-agent decides]**.

---

## 1. Architecture

```
  MacBook (CLI app = prover frontend)        Interlock (MPF300)            Prover compute
  ┌───────────────────────────────┐          ┌───────────────┐            ┌──────────────────┐
  │ drive requests                │  802.3   │ port 0        │   802.3    │ port 1           │
  │ log every packet + cert       │◀────────▶│ (frontend)    │◀──────────▶│ model server +   │
  │ verify certs                  │  Ethernet│ canonicalize  │  Ethernet  │ ZKP prover       │
  │ challenge → reveal → verify   │          │ + sign certs  │            │                  │
  └───────────────────────────────┘          └───────────────┘            └──────────────────┘
```

Roles (these mirror `certificate-protocol.md` §7):

- **MacBook / CLI app = prover frontend *and* (for this demo) the verifier.**
  Originates inference *requests*, receives *responses* + *certificates*, keeps the
  full log (record-keeping), and — because it also plays the verifier here — **holds
  the cert HMAC key and fully verifies every certificate** before issuing challenges.
  It is the only piece you build. (In production these two roles separate: the verifier
  that provisioned the interlock holds the key; the prover frontend does not — see §3.)
- **Interlock (MPF300).** Canonicalizes both directions and emits **one signed
  certificate per packet**. Fixed; treat as a black box with the wire contract in §2–§3.
- **Prover compute.** Runs the model (produces responses) and, on challenge, reveals
  the plaintext transcript + a ZK proof. **You own this side.**

The MacBook cables to interlock **port 0**; the prover compute cables to **port 1**.
The app never talks to the prover-compute box directly for the *data path* (all
inference traffic goes through the interlock); it talks to it directly only for the
**challenge/ZKP channel** (§6).

---

## 2. Interlock wire contract (verified on silicon)

- **802.3 LENGTH frames only.** The L/T field must be the DATA length, `≤ 1500`.
  Frames with L/T `> 1500` (Ethernet II / TYPE) are **dropped**.
- **Canonicalization / forced addressing.** The interlock overwrites DST/SRC MACs and
  regenerates LENGTH + FCS. Forced values: **client `02:00:00:00:00:01`**, **server
  `02:00:00:00:00:02`**. So your chosen DST/SRC are discarded; you *identify*
  interlock-emitted frames by these forced MACs, which also proves a frame actually
  traversed the bridge.
- **Direction.** Requests flow port 0 → port 1 (forced DST = server `..:02`).
  Responses flow port 1 → port 0 (forced DST = client `..:01`). **Certificates (for
  both directions) egress port 0** alongside the responses.
- **Cable mapping is per-setup and currently SWAPPED.** On the current rig port 0 =
  the USB-Ethernet dongle, port 1 = the built-in NIC. On the MacBook you have one
  adapter cabled to port 0; confirm orientation empirically (send a packet, see where
  the forced-MAC frames come back) — don't hardcode it.
- **Packet = `[16-byte header] [encrypted payload]`.** The current build treats the
  first 16 bytes as an opaque header and the rest as ciphertext. The *semantic* header
  fields (request ID, bucket number, reference request ID, recomputation commitment)
  are defined in `certificate-protocol.md` §2; lay them out within those 16 bytes (or
  widen `HDR_BYTES` in a future build).

### ⚠ Hard constraint: one packet at a time, with a gap

This build makes **a certificate per packet**, and each cert is an HMAC with **no
back-pressure** (a pulse sink). If a second packet's hash arrives while the previous
cert's HMAC is still running, the cert path **wedges the whole interlock** — both
Ethernet links drop (carrier → 0) and it needs a **power cycle** to recover. A
back-to-back flood reproduces this every time.

**The app MUST serialize packets** with a safe inter-packet gap. Verified clean at
**300 ms, strictly one-at-a-time**; the safe minimum is not yet characterized, so make
the gap a config knob and default conservative. (A future clock-based build removes
this constraint; until then, treat "rate-limit to one in-flight packet" as a hard
requirement, not a tuning option.)

---

## 3. Certificate format (verified byte-exact)

Cert frame on the wire (egresses port 0; DST `02:..:01`, SRC `02:..:02`, **802.3 LEN
= 148 = 0x0094**). After the 14-byte Ethernet header, the 148-byte DATA is, all
**big-endian**:

| offset (in DATA) | field | bytes | value in test build |
|---|---|---:|---|
| 0   | reserved cert header (zeros; leading id=0 flags "this is a cert") | 16 | all `00` |
| 16  | version            | 4  | `0x00000006` |
| 20  | interlock_id       | 4  | `0x00000042` |
| 24  | bucket_start       | 8  | monotonic counter (per direction) |
| 32  | num_buckets        | 4  | `0x00000001` |
| 36  | overall_req        | 32 | request hash (0 in a response cert) |
| 68  | overall_rsp        | 32 | response hash (0 in a request cert) |
| 100 | nonce              | 16 | `0xFF…FF` in test build |
| 116 | tau = HMAC-SHA256  | 32 | the signature |

- **Request cert** ⇒ `overall_rsp == 0`. **Response cert** ⇒ `overall_req == 0`.
- The **signed message** is `m = DATA[16:116]` (version … nonce, 100 bytes).
- **HMAC key.** The interlock holds the cert key and it never leaves the device. **For
  this demo the MacBook also holds the verifier role, so it is given the cert key and
  checks `tau` directly** — in the test build that key is the constant `0x00…02`
  (`cert_build .key(2)`). Keep it a configured secret (not baked in): in production the
  verifier role — and the key — live with the party that provisioned the interlock,
  separate from the prover frontend, and the frontend would treat the cert as an opaque
  attestation it forwards rather than checks.

### Hash hierarchy (verified — the app must recompute this)

```
H(payload)      = SHA256(ciphertext)                              # ciphertext = DATA after the 16B header
record_digest   = SHA256(header[16] || H(payload))
overall         = SHA256( be16(datalen) || record_digest )       # datalen = the packet's 802.3 LEN; num_buckets=1 ⇒ one record
```

So `overall_req` / `overall_rsp` in a cert binds the exact on-wire packet. The app,
from its **logged ciphertext**, recomputes `overall` and checks it equals the cert
field — proving the cert commits to the traffic the app actually saw.

---

## 4. Reference code (ported from the verified bring-up scripts)

These are the tested building blocks (currently on the Spark at
`~/fpe/cert_send_spaced.py` and `~/fpe/cert_parse.py`). Port them to the MacBook;
the only platform change is the raw-Ethernet transport (§7).

**Frame a packet (802.3 LENGTH):**
```python
def frame(data: bytes) -> bytes:        # data = header(16) || ciphertext
    dst = b"\xde\xad\xbe\xef\x00\x01"   # arbitrary; interlock forces to 02:..:0X
    src = b"\xde\xad\xbe\xef\x00\x02"
    f = dst + src + len(data).to_bytes(2, "big") + data   # DST SRC LEN DATA
    return f + b"\x00" * (60 - len(f)) if len(f) < 60 else f
```

**Identify + parse a cert** (a captured frame `fr`):
```python
def parse_cert(fr):
    if len(fr) < 14 + 148 or fr[6:12] != bytes.fromhex("020000000002"): return None
    if int.from_bytes(fr[12:14], "big") != 148: return None
    d = fr[14:14+148]
    return dict(reserved=d[0:16], version=int.from_bytes(d[16:20],"big"),
                interlock_id=int.from_bytes(d[20:24],"big"),
                bucket_start=int.from_bytes(d[24:32],"big"),
                num_buckets=int.from_bytes(d[32:36],"big"),
                overall_req=d[36:68], overall_rsp=d[68:100],
                nonce=d[100:116], tau=d[116:148], m=d[16:116])
```

**Verify a cert** (key known):
```python
import hmac, hashlib, struct
def verify_tau(c, key):           # key = 32 bytes, test build = (2).to_bytes(32,"big")
    return hmac.new(key, c["m"], hashlib.sha256).digest() == c["tau"]

def overall(header, ciphertext, datalen):
    rec = hashlib.sha256(header + hashlib.sha256(ciphertext).digest()).digest()
    return hashlib.sha256(struct.pack(">H", datalen) + rec).digest()
```

Both checks pass on silicon for every captured cert (request and response).

---

## 5. CLI app design

Suggested surface (adjust to house style):

```
infcli send      --prompt "<text>" [--gap-ms 300]     # encrypt → frame → send 1 request,
                                                        # await response + certs, log all, print request-id
infcli log                                              # list logged (request-id, time, cert summary)
infcli show      <request-id>                           # decoded request+response packets + their certs
infcli verify    <request-id>                           # re-check certs: tau (HMAC) + overall (hash) binding
infcli challenge <request-id>                           # the full reveal+ZKP flow of §6; prints the match report
```

Global config: `--iface`, forced MACs (for filtering), the cert key (secret),
`--gap-ms`, and the **prover challenge endpoint** (§6). Persist a log of every packet
(both directions, **ciphertext as seen on the wire**) and every cert — the app must
keep the exact bytes so cert and proof checks are reproducible later.

**Sending discipline:** never have more than one packet in flight; wait
`gap-ms` (and ideally until the matching cert is captured) before sending the next.

---

## 6. Challenge → reveal → ZKP → match  *(the part you build on the ZKP side)*

A **challenge** opens one logged packet: it asks the prover to *reveal* that packet's
plaintext and *prove* it was computed correctly, then the app checks the revealed
plaintext against both the certificate and the proof. This is a deliberate, selective
de-anonymization of a single packet on demand (the rest stay sealed).

### 6.1 What the user must see (your requirement)

For a challenged packet the app prints:
1. **Plaintext of the challenged packet** — the app *itself* decrypts the
   certificate-bound ciphertext from its log (it does not just trust the prover's copy).
2. **Plaintext of the inference transcript the ZKP attests** — prompt + completion (and
   whatever intermediate transcript the proof covers) as supplied by the prover.
3. **A match check** between (1) and (2), plus the full cryptographic chain below, with
   a single clear PASS/FAIL.

### 6.2 The end-to-end verification chain (app logic)

For challenged request packet *Q* and its paired response *R*:

```
(a) cert authenticity   tau == HMAC(key_interlock, m)                  for cert(Q) and cert(R)
(b) cert ⇒ ciphertext   overall(header,ct,len) == cert.overall_*       recompute from the LOGGED ciphertext
(c) ciphertext ⇒ key    H(revealed_key) == recomputation_commitment    (commitment lives in Q's header, proto §2)
(d) decrypt             plaintext = Decrypt(revealed_key, ct)          app decrypts the logged ciphertext itself
(e) plaintext match     plaintext(Q) == zkp.transcript.input           ← the user-visible check
                        plaintext(R) == zkp.transcript.output          ← the user-visible check
(f) proof validity      ZKP.verify(proof, public_inputs) == true       transcript = Model(input) (+ info bound)
(g) proof binding       zkp.public_inputs commit to the same transcript checked in (e)
```

Reading it end to end: the cert (a) is genuine and (b) binds the ciphertext the app
logged; (c)+(d) decrypt that exact ciphertext to plaintext using the *committed* key;
(e) shows the plaintext equals the proof's transcript; (f) proves that transcript is
the correct model output; (g) ties the proof to that transcript. So **what was
certified on the wire decrypts to a transcript that was provably computed correctly** —
and the user sees both plaintexts and their equality.

If any step fails, the app must say *which* — e.g. "(e) MISMATCH: response plaintext ≠
proof output" is a very different finding from "(f) proof invalid."

### 6.3 Interface you need to define **[ZKP-agent decides]**

- **Challenge transport.** App → prover request identifying the packet (by request ID
  **and** the cert's `overall_req`/`overall_rsp`, so the prover answers about the exact
  certified bytes) and getting back `{revealed_key, transcript, proof, public_inputs}`.
  Out-of-band TCP/gRPC to the prover-compute box is simplest; an in-band special packet
  is also possible. Your call.
- **What the proof proves.** At minimum: `transcript.output = Model(transcript.input)`
  for the agreed model, plus whatever unexplained-information bound the repo already
  establishes. Reuse the existing prover/verifier; this app only needs the verifier +
  a way to extract/compare the transcript.
- **Public-input encoding & binding.** The proof's public inputs must commit to the
  transcript (and, ideally, directly to `overall_req`/`overall_rsp`) so that (g) is a
  real check, not a trust step. Specify the exact encoding the app will compare against.
- **Encryption scheme & key reveal.** Define `Decrypt`, how `recomputation_commitment`
  commits the key (proto §2 suggests `H(key)`), and what "reveal" exposes for a
  challenged packet vs. what stays zero-knowledge for un-challenged ones.
- **Transcript ↔ packet alignment.** Specify how a (possibly multi-packet, multi-turn)
  transcript maps to the single challenged packet — relevant once `reference request
  ID` multi-turn flows (proto §2) are in play. For the initial single request→response
  pair this is 1:1.

---

## 7. macOS / implementation notes

- **Raw 802.3 on macOS:** there is no `AF_PACKET`. Use **libpcap** (`pcap_inject` to
  send, a capture handle to receive) or **scapy** (`sendp` / `AsyncSniffer`, which wrap
  libpcap). Both need access to `/dev/bpf*` (run with sufficient privilege).
- **Interface:** a USB-Ethernet / Thunderbolt adapter cabled to interlock port 0 (e.g.
  `en7`). Confirm port orientation empirically (§2).
- **Capture filter:** sniff frames with `ether src 02:00:00:00:00:02` to get all
  port-0 egress (responses + certs); a cert is the subset with **802.3 LEN 148**;
  a response is your data length.
- **Spacing in software:** enforce the one-in-flight + gap rule (§2) in the send loop;
  this is a correctness requirement, not just politeness.
- **Logging:** persist exact on-wire ciphertext bytes + parsed certs per request ID, so
  `verify` and `challenge` are reproducible offline.

---

## 8. Bring-up plan

- **Phase 0 — loopback against the current test build, no real prover.** Port
  `cert_send_spaced.py` + `cert_parse.py` to the MacBook; send single packets through
  the interlock and confirm capture → parse → `tau` PASS → `overall` PASS on macOS.
  This reproduces the verified Spark result (`6/6 tau`, `6/6 overall`) from the new
  transport, and shakes out libpcap/permissions/port-mapping before any ZKP work.
- **Phase 1 — real prover behind port 1.** Replace synthetic packets with real
  encrypted requests; the prover-compute box returns real responses; confirm
  per-packet certs still verify and the app logs full request/response pairs.
- **Phase 2 — challenge.** Implement §6: reveal + decrypt + plaintext match + ZKP
  verify + binding, and the PASS/FAIL match report. Start with a single
  request→response pair (1:1 transcript) before multi-turn.

Keep the **one-packet-at-a-time** discipline throughout — a flood wedges the interlock
and costs a power cycle (§2).

---

## 9. References

- `certificate-protocol.md` — packet/cert formats, challenge protocol, time-bracketing,
  prover↔interlock dataflow (the protocol this app instantiates).
- `verification-protocol.md`, `interlock-protocol.md` — surrounding protocol context.
- `~/fpe/HARNESS.md` (Spark) — the build→flash→test rig and the bring-up that produced
  the verified facts here.
- Verified silicon result (2026-06-18): per-packet certs, `6/6 tau` + `6/6 overall`,
  one-at-a-time @300 ms, cables swapped (port 0 = dongle).
