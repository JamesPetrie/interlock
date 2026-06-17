# Interlock certificate & packet protocol

Snapshot (2026-06) of the interlock's **packet, hashing, and certificate formats**,
the **challenge protocol**, the **time-bracketing argument**, and the **prover ↔
interlock dataflow**.

**Scope of the initial implementation:** single **input → output packet pairs**
(one request, one response) — *not* multi-step exchanges or payloads spanning
several packets. The `reference request ID` field (below) is in place so that
multi-turn / multi-packet flows can be added later without a format change.

See also: [`bucket-declaration-spec.md`](bucket-declaration-spec.md) (the
`bucket number` field, exact-match drop, and tick beacon) and
[`interlock-protocol.md`](interlock-protocol.md) / [`verification-protocol.md`](verification-protocol.md).

---

## 1. Threat-model assumptions

The interlock is assumed to be:

- **Reset-resistant** — it cannot be forced to restart at `bucket = 0`; the bucket
  counter is monotonic (battery-backed with the key).
- **Speedup-resistant** — beyond roughly **2×** speedup.
- **HMAC-secret-protecting** — the certificate HMAC key never leaves the interlock.
- **Tamper-proof internal memory** — running state (counters, hash contexts, key)
  cannot be tampered with.

These underpin the time-bracketing argument in §5.

---

## 2. Packets

Every packet is `header ‖ encrypted payload`. All header integers are big-endian.

### Input (request) packet

| field | purpose |
|---|---|
| length | payload length |
| request ID | unique, monotonically increasing id |
| bucket number | prover-declared bucket |
| reference request ID | references a previous request whose data this request uses (see below) |
| recomputation commitment | hash of the cryptographic info needed to recompute the output (e.g. `H(key)`) |
| encrypted payload | the ciphertext |

### Output (response) packet

| field | purpose |
|---|---|
| length | payload length |
| request ID | the request ID of the input it is paired with |
| bucket number | prover-declared bucket |
| encrypted payload | the ciphertext |

The output packet carries **no** recomputation commitment (recomputation is keyed
off the input) and **no** reference request ID.

> **Request ↔ response pairing is by request ID:** a response carries the same
> request ID as the input it answers, so no separate reference field is needed on
> the output.
>
> **Reference request ID** (request packets only) lets a request **use data from a
> previous request** — e.g. a multi-turn exchange where a later request depends on
> an earlier one. The initial implementation only exercises single input→output
> pairs; committing this field now lets such cross-request dependencies be expressed
> later without a format change.

### Packet hash

```
H(PACKET) = H( HEADER ‖ H(CIPHER) )
```

The ciphertext is hashed separately as a **separable leaf** (`H(CIPHER)`), so a
challenge can open `HEADER ‖ H(CIPHER)` and prove a packet's identity **without
revealing the plaintext**. The interlock computes `H(PACKET)` itself (and the
prover re-derives it for its log) — it is never trusted from the wire.

---

## 3. Overall hash & certificate

### Overall hash

Each certificate commits, **per direction**, a single **flat hash** over the
`(length, packet_hash)` pair of every packet in **this certificate's buckets** (the
`num_buckets` buckets starting at `bucket_start`), concatenated directly in
transmission order:

```
overall_in  = H( (length, packet_hash) ‖ (length, packet_hash) ‖ … )
                 -- every input packet in this certificate's buckets, in order
overall_out = H( (length, packet_hash) ‖ (length, packet_hash) ‖ … )
                 -- every output packet in this certificate's buckets, in order
```

- `length` lets the verifier index a randomly-selected byte position (size arithmetic).
- `packet_hash = H(HEADER ‖ H(CIPHER))` is the per-packet commitment (§2), hiding the
  contents until opened.

Only `length` and `packet_hash` enter the hash — **not** the request ID or other
header fields — so an opening exposes packet *sizes* and *hashes* but not request IDs
or contents. (The bucket number and the rest of the header stay committed indirectly,
inside `packet_hash` via the header.)

Buckets are the **timing / windowing** concept — each packet carries its bucket
number (§2) and a certificate spans `num_buckets = 1000` of them — they are not a
layer in the hash.

### Interlock certificate

| field |
|---|
| version |
| interlock id |
| freshness nonce |
| bucket start |
| num_buckets ( = 1000 ) |
| overall_in  (flat hash of the input `(length, packet_hash)` pairs) |
| overall_out (flat hash of the output `(length, packet_hash)` pairs) |
| HMAC of the certificate contents |

The certificate commits the two overall hashes; the **ordered `(length, packet_hash)`
pairs** are revealed by the prover at challenge time (§4), not carried in the cert.

---

## 4. Challenge protocol

```
1. Verifier → Prover           : a fresh nonce.
2. Prover  → Interlock         : feeds the nonce in.
3. Interlock → Prover → Verifier: an interlock certificate carrying that nonce
                                  (this reveals the recent bucket number).
4. Verifier → Prover           : randomly selects a single byte x in the
                                  certificate's COMBINED buckets (all transmitted
                                  bytes across its num_buckets buckets, one
                                  direction) — challenge "byte x".
5. Prover  → Verifier          : evidence of EITHER
                                    (a) the packet occupying byte x had hash z, OR
                                    (b) byte x was empty (past the total length of the
                                        committed packets);
                                  plus the opening material:
                                    - the ordered (length, packet_hash) pairs (so the
                                      verifier can recompute the overall hash),
                                    - the values used to compute the queried packet
                                      hash (header and H(cipher)).
6. Input binding               : the prover also supplies the certificate of the
                                  INPUT paired with the challenged output (the input
                                  carrying the same request ID), binding the output
                                  to a real, single-use request.
```

Byte x is a single position over the certificate's **combined buckets** (per
direction) — not within any one bucket; the size-weighted random selection samples
*transmitted bytes* uniformly. The revealed `(length, packet_hash)` pairs let the
verifier recompute the overall hash and locate the packet covering byte x by summing
lengths across the combined stream.

> **Note:** the challenge is a single byte position over the combined buckets — there
> is no per-bucket localization — which matches the flat hash (both operate over the
> certificate's whole stream). An opening therefore reveals **all** `(length,
> packet_hash)` pairs for that direction.

---

## 5. Time bracketing

Because the certificate carries the verifier's nonce, the verifier knows the cert
was generated **after** the nonce was issued and **before** the cert was received —
bracketing the wall-clock window in which the committed state existed. Combined with
**reset-** and **speedup-resistance**, this bounds how much work the prover could
have inserted in that window.

> **Open question:** is nonce → certificate
> round-trip bracketing **sufficient** given the reset / speedup-resistance
> assumptions? — to analyze.

---

## 6. I/O timing discipline

To keep packet **timing** from becoming a side channel:

- **Writes are always allowed.**
- **Read buffering** is sized by `f( max pipeline latency , max packet write time )`
  — reads are held in a bounded buffer so their timing doesn't leak pipeline state.
- Reads follow a **Read A → wait → Read B** pattern.

> **TODO / needs detail:** the exact semantics of "read" vs "write" here, and the
> precise Read A / Read B timing, were not fully specified in the source notes.
> Documented at the sketch level above; to be pinned down (the intent is to bound
> the read-timing channel).

---

## 7. Responsibilities & log format

| component | responsibilities |
|---|---|
| **Interlock** | ethernet communication; information isolation; information certificates |
| **Prover frontend** | record keeping; ethernet |

**Log format (to align on): a list of packets.** Requirements:

- The prover frontend must be able to **recreate the exact serial representation of
  the buffer** from the union of the packet descriptions in its log.
- The tap's output must depend **only** on information already in the buffer plus
  newly-generated information (no hidden state) — so the log **fully determines** the
  committed certificate, and the prover's recomputation matches the interlock's
  byte-for-byte.

---

## 8. Prover ↔ interlock dataflow

Two mirrored pipelines (request and response), each `deframe → check length +
request ID → ping-pong buffers → commit → reframe`. A `certificate` node bridges
them, fed by the response commit, the request commit, and the nonce (from a
check-length stage). The link to the prover compute and the link to the network
are each **shared** (multiple flows multiplexed onto one physical resource).

```mermaid
flowchart TB
    subgraph REQ["Request pipeline (network → prover)"]
      direction TB
      qi(["from network"]) --> qd["deframe"] --> qk["check len + request ID"] --> qp["ping-pong buffers"] --> qc["request commit"] --> qr["reframe"] --> qo(["to prover compute"])
    end

    subgraph RSP["Response pipeline (prover → network)"]
      direction TB
      ri(["from prover compute"]) --> rd["deframe"] --> rk["check len + request ID"] --> rp["ping-pong buffers"] --> rc["response commit"] --> rr["reframe"] --> ro(["to network"])
    end

    CERT["certificate"] --> co(["cert → network"])
    rc -. response root .-> CERT
    qc -. request root .-> CERT
    qk -. nonce .-> CERT
```

The two pipelines are drawn as **independent parallel lanes**, both flowing
top→bottom; the certificate sits beside them, fed on dotted edges.

- **Request lane** (network → prover): `deframe → check → ping-pong → request
  commit → reframe`.
- **Response lane** (prover → network): `deframe → check → ping-pong → response
  commit → reframe`.
- **Certificate**: produced from the response commit + request commit + the nonce
  (taken from the request-pipeline check stage), emitted to the network.
- The **ping-pong buffers** are identical double-buffers on both lanes.

**Shared physical links** (kept out of the diagram so the lanes stay parallel):
the **prover-compute side** is one shared ethernet link carrying both the request
(down) and the response (up); the **network side** is one shared bus carrying the
response, the certificate, and the request. The lane endpoints (`from/to network`,
`from/to prover compute`, `cert → network`) mark where each lane taps those links.

> **To confirm:** (i) whether the nonce is taken from the request-pipeline check
> stage or a dedicated stage; (ii) whether the network-side bus's three flows
> (response, certificate, request) share one physical link while staying logically
> separate (assumed here) vs. a single merged output. The unlabeled fan-out boxes
> are the ping-pong double-buffers.

