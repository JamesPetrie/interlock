# Bucket-declaration spec (design A: exact match)

## Problem

The certificate commits per-bucket digests; buckets are time windows. For the
prover frontend's **log** to recompute byte-identically to the **certificate**,
prover, interlock, and verifier must agree on *which bucket each packet is in*.
Today that assignment is implicit — a packet lands in "whatever bucket counter is
current when the interlock sees it" (`interlock_core` free-running `bucket`,
`interlock.py` `self.bucket`). Any party that re-derives this from its own clock
disagrees with the interlock near a window boundary, producing a log that doesn't
match the cert *for timing reasons* — a nondeterministic, hard-to-debug failure.

## Design: prover proposes, interlock validates (exact match)

The prover writes its **intended bucket** into an unencrypted header field. The
interlock checks that declaration against its own trusted bucket counter and
**drops on any mismatch**. Downstream, the frontend and verifier **group by the
declared field** — they never re-derive a boundary.

Three properties hold simultaneously:

- **FPGA stays O(1).** Per packet: read the declared bucket, compare to the
  trusted counter, drop or fold into the single current-window digest. No log in
  memory — identical state to today.
- **Log is unambiguous.** The bucket is an explicit, committed field; grouping is
  a lookup, not a timing computation. The only things recomputed downstream are
  content hashes and the existing format checks, both pure functions of the bytes.
- **Forward path stays byte-exact.** The *prover* writes the field; the interlock
  reads and validates but never modifies the frame. "Same packet in/out" holds.

### Why "exact" is sound, not brittle

Because acceptance requires `declared == interlock's current bucket`, an accepted
packet's recorded bucket *is* the true-time bucket. The field therefore carries
exactly the timing the interlock already measures — **zero** extra degrees of
freedom, so it is accounting metadata, not a new exfiltration channel, and is
**not** counted toward unexplained info.

A drop is never part of honest operation: the honest prover declares the correct
bucket (it slaves its clock to the interlock's tick beacon and tracks the fixed
tick rate; see *Clock distribution* and *Guard interval*), so nothing is dropped
and `log == accepted == cert`. Any deviation — clock drift, mis-declaration,
malformed frame, or active
attack — silently drops the packet, so the verifier recomputing from the log gets
a digest mismatch and rejects. That is the desired behavior, and it needs no drop
telemetry (consistent with the no-observables rule).

## Wire format change

Add an 8-byte big-endian `bucket` field to both headers, immediately after
`request_id`. (8 bytes matches the interlock's 64-bit monotonic counter; at 1 ms
ticks a 64-bit counter never wraps.)

| direction | layout | size |
|---|---|---|
| in  | `length(4) \vert request_id(8) \vert bucket(8) \vert recomp_commitment(32)` | 52 B |
| out | `length(4) \vert request_id(8) \vert bucket(8)` | 20 B |

(Was 44 B / 12 B; both grow by exactly 8 B, so the in−out header difference stays
32 B — this keeps the gateware left-justify shift constant unchanged; see below.)

The `bucket` value is the **absolute** interlock bucket number the prover expects
this packet to be accepted into.

### What commits the field

`packet_hash = H(header || H(ciphertext))` already hashes the whole header, so the
declared bucket is committed inside `packet_hash` → inside the per-packet
`record` → inside the bucket digest of *that* bucket → inside the cert. The prover
cannot later reassign the packet's bucket without breaking the hash chain, and the
interlock guaranteed `declared == true` at accept time. **`RECORD` is unchanged**
(`length \vert request_id \vert packet_hash`) — no need to duplicate the bucket;
the verifier reads it from the logged packet's header.

## Validity rule

Add one clause to the accept predicate, alongside the existing length / id /
capacity checks:

```
accept  ⇔  length == len(ciphertext)
        ∧  length ≤ s_max
        ∧  request_id > last_id           (monotonic per direction; out resets per bucket)
        ∧  used + packet_len ≤ capacity
        ∧  declared_bucket == bucket        ← NEW (exact match)
```

`bucket` is the interlock's current counter value at the moment the packet is
validated (it increments only at a boundary, so it is well-defined between ticks).

## Clock distribution (tick beacon)

The prover must know the interlock's current bucket number and tick phase to
declare correctly. The interlock is the authoritative clock; the prover **slaves**
to it. Distribution is a **one-way broadcast** from the trusted side — never a
prover-initiated query (a query would be a prover-influenceable observable).

The interlock emits a small **tick beacon** on the existing cert egress channel
(interlock → prover frontend) every `stride` ticks. Beacon arrival is a phase
edge; the body gives the absolute bucket number starting at that edge. This
decouples sync rate from cert-window size `N`: the cert window stays whatever the
protocol wants while the beacon runs at whatever rate the prover's oscillator
needs (every 10–50 ms keeps a 50 ppm crystal within a few µs against 1 ms ticks).

### Beacon frame

A standard Ethernet frame, distinct DST so the frontend demuxes it from cert
frames (`02:..:CE`) and forwarded traffic:

| field | bytes | value |
|---|---|---|
| DST | 6 | `02:00:00:00:00:CB` (beacon) |
| SRC | 6 | `02:00:00:00:00:CF` (interlock, same as cert) |
| len | 2 | 32 |
| magic | 8 | `"ilbcn-v1"` |
| interlock_id | 8 | matches the cert `iid` |
| bucket | 8 | absolute bucket index starting at this edge |
| tick_period_ns | 4 | nominal bucket width (e.g., 1_000_000) |
| stride | 4 | ticks between beacons |
| (pad to 46 B min payload + FCS by the MAC) | | |

`tick_period_ns` and `stride` are constants but carried in-band so the prover
needs no separate config to stay in sync. **No HMAC** — the beacon is *not*
soundness-critical: a prover that fakes its own clock reference only mis-times and
drops itself, failing verification. (A tag can be added later as on-medium-noise
insurance; it is not a security requirement.) Keeping it unauthenticated keeps the
emitter trivial — a tick counter + framer, no SHA engine.

### Prover PLL

The prover runs a software PLL: on each beacon, latch `bucket` and the arrival
time (minus the calibrated fixed egress offset) as the phase reference; between
beacons, interpolate with the local clock at `tick_period_ns`; place each send in
its intended bucket with the *Guard interval* below. A dropped or
arbitration-delayed beacon is harmless — the PLL averages, and the next beacon
re-anchors. Bootstrapping is free: the bucket counter is battery-backed, so the
prover waits for the first beacon, locks on, then begins sending.

### Threat-model note

The beacon is safe to broadcast and stays **out of the unexplained-info budget**:
it is interlock → prover (not exfiltration, which is prover → out); the bucket
number is just *time*, nothing secret; and precise phase only lets the prover hit
its intended bucket and avoid drops — it does **not** raise timing-channel
capacity, which is fixed by the 1 ms bucket granularity.

## Guard interval (honest-prover obligation)

Exact match means a packet that crosses a window boundary between the prover and
the interlock is dropped. The honest prover avoids this by **not emitting within a
guard interval before each tick**. The guard must cover:

- clock skew between prover and interlock,
- prover→interlock propagation delay,
- the interlock's boundary-processing latency (the `bucket` register lags the
  physical tick by the ~280-cycle boundary FSM; a packet arriving in that window
  is still checked against the old counter).

With 1 ms ticks and a ~2.2 µs boundary FSM (at 125 MHz), the guard is a tiny
fraction of a window, so honest drops are avoidable. **The honest drop rate is an
empirical question we measure once flashed**; if it's nonzero in practice we
revisit tolerance (design B), but A is the default.

## Implementation deltas

### `prototype/wire.py`
- `HEADER["in"]  = [length, request_id, bucket, recomp_commitment]`
- `HEADER["out"] = [length, request_id, bucket]`
- `input_packet(request_id, bucket, key, ciphertext)` and
  `output_packet(request_id, bucket, ciphertext)` gain the `bucket` arg.
- `HDR`, `parse_packet`, `packet_hash`, `record` follow automatically from the
  layout tables (no logic change — `record` still emits `length|request_id|
  packet_hash`, now over the longer header).

### `prototype/interlock.py`
- `on_packet`: add `if p["bucket"] != self.bucket: return None` as the **first**
  validity check (drop before folding; mismatched packets are never hashed).

### `gateware/.../pkt_record.v`
- Widen the header buffer `hdr` 352→416 bits and `fbuf` likewise; `hdr_len =
  dir ? 20 : 52`; capture `request_id` for `hcnt` 4..11 (unchanged) and a new
  `bucket_id[63:0]` for `hcnt` 12..19; expose `bucket_id`. `left_hdr` shift stays
  `dir ? (hdr << 256) : hdr` (the 32-byte in−out gap is unchanged).

### `gateware/.../interlock_core.v`
- Consume `pr_bucket_id`; add `wire bad_bucket = (pr_bucket_id != bucket);` and
  `assign accept_w = !bad_len && !bad_id && !bad_cap && !bad_bucket;`.
- No change to the bucket/cert FSM, the four SHA contexts, or the cert body — the
  certificate format is identical; only the accept gate tightens.

### `gateware/.../eth_deframe.sv`
- No change to canonicalization: the bucket field rides inside the payload the
  prover sends; deframe already passes payload bytes through to the core. (The
  field is part of the agreed packet format, not an Ethernet header field.)

### Clock distribution (new)
- **`gateware/.../tick_beacon.v`** (new) — counts `bucket_tick`; every `stride`
  ticks asserts a 32-byte beacon body (`magic|iid|bucket|tick_period_ns|stride`)
  into a framer (reuse `cert_framer` with DST `02:..:CB`, no HMAC) → a **new,
  lowest-priority** input on `mac_tx_mux`. Beacons are tiny and droppable, so they
  never delay forwarding or certs; the prover tolerates the resulting jitter.
- **`mac_tx_mux.v`** — extend 3→4 inputs (forwarded, req cert, rsp cert, beacon),
  beacon lowest priority.
- **`prototype/` prover PLL** — `frontend.py` gains a beacon listener + PLL that
  tracks `bucket`/phase and a `current_bucket()` used to stamp the declared field,
  honoring the guard interval.

## Differential test (now aligned by construction)

Both engines bucket strictly by the declared field and both enforce
`declared == own counter`. With a **shared tick source** driving the FPGA
`bucket_tick` and the Python `on_bucket_boundary` together, the two counters stay
equal, every honest packet is accepted into the declared bucket on both sides, and
the certs are byte-identical. The harness:

1. Generate traffic: packets with explicit `request_id` + `bucket`.
2. Feed identical bytes to the FPGA (over the wire) and to `interlock.py`.
3. Drive ticks/boundaries from one source so both counters advance together.
4. Capture the FPGA cert frame (DST `02:..:CE`) and compute the Python cert.
5. Assert equal (140 B, body + HMAC).

No manifest, no clock reconciliation: the one timing-sensitive decision is made
once (the declared field, validated by the interlock) and read by everyone else.

## Out of scope here

- Real verifier nonce/challenge (still latched/hardcoded in the prototype).
- Tolerance (design B) — only if honest drops prove nonzero on hardware.
- Gating PROTOTYPE_DEBUG taps out of the production bitstream.
