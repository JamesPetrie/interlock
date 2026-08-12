# Recomputation Interlock Core Design Specification

This document describes the **recomputation interlock core** (`recomp_ilock_core.sv`) — implementing the recomputation interlock of `verification-protocol.md`: it mediates everything crossing the recomputation-enclosure boundary, commits the staged challenge slice, runs the estimate/reveal loop, and attests the result. Block internals live in their own docs.

```
                                                        ┌───────────┐
                                                        │   bucket  │
                                                        │   timer   │
                                                        └───────────┘
  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──  ┬  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──
(x,o) in                                       outside region   inside region
                                                              │
        ┌──────────┐  ┌────────────┐    ┌────────────┐  ┌───────────┐    ┌────────────┐  ┌──────────┐
MAC0──▶ │   eth    │─▶│ canon proc │───▶│   traffic  │─▶│  buc│ket  │───▶│  recomp    │─▶│   eth    │──▶ MAC1
FIFO    │ deframe  │  │ + drop*    │    │   commit   │  │  buf fer  │    │  feed      │  │ reframe  │    FIFO
        └──────────┘  └────────────┘    └──────┬─────┘  └─────│─────┘    └─────┬──────┘  └──────────┘
tuser:                      │      len@beat#0  │  len@beat#0       len@beat#0  │  ▲  len@beat#0
                            │     swap(inline) │ swap(inline) │   swap(inline) │  │
                           s│                  │                               │  │
                           y│                  │              │                │  │
                           n│                  │  ┌────────────────────────────┘  │
                           c│                  │  │           │                   │
(m,τ) out                   │                  │  │                               │
                            │                  │  │           │                   │
                            ▼                  ▼  ▼                               │
        ┌──────────┐  ┌────────────┐    ┌───────────┐         │                   │      ┌──────────┐
MAC0◀── │   eth    │◀─│  2×1 mux   │◀───│   cert    │                             └──────│   eth    │◀── MAC1
FIFO    │ reframe  │  │            │    │   build   │         │                          │ deframe  │    FIFO
        └──────────┘  └────────────┘    └───────────┘                                    └──────────┘
tuser:           len@beat#0                                   │

* drop: a separate axis_pkt_gate after canon proc
```

## Deployment — bump on the enclosure boundary

Port 0 faces the **prover frontend**, port 1 the **recomputation enclosure**; forced station addresses (frontend `.01`, compute `.02`), one fabric clock, MAC FIFOs handle the line-side CDC. There is **one processed direction**: the challenge slice flows 0 → 1 through the full pipeline, while the return path (1 → 0) carries only estimate frames, consumed whole by `recomp_feed` — nothing from the enclosure is ever forwarded to the frontend. The frontend-bound egress carries exactly two streams: certificates and sync packets.

The frontend's side of the contract:

- stage the challenge slice as canonical packets — the **challenged response first**, then its context into a single bucket. That position is the whole of what identifies the challenged packet, so no other data packet may precede it;
- carry the **challenge nonce** in a **NONCE marker** (a header-only `ID = 0` packet) and the **expected slice commitment** in an **EXP marker** (a header-only `ID = 1` packet), both in `KEY_COMMIT`. Both are **consumed, not forwarded** — the reserved IDs fail admission while their fields still latch — so they may sit anywhere inside or before the bucket, and neither enters the digest it arms;
- stamp the **current bucket** — sync packets arrive back on port 0 for calibration, and bucket integrity gates admission;
- hold staging while a challenge runs: packets arriving mid-challenge are committed upstream but dropped whole by the feed, so a violation reads as lost packets in the digest, never as corrupted framing;

LIMITATION: Because a slice occupies a whole bucket, the 320 KB bank is a flat cap of **~320 KB per challenge slice**.

## Ingress path

The ingress is `deframe → canon_proc → drop gate → traffic_commit → batch_buffer` — so the slice is checked, committed, and bucketed exactly as the original traffic was. That reuse is what makes the slice commitment comparable: the verifier recomputes the same hierarchy from the opened production records and checks equality (see the protocol's Option 1). `canon_proc` runs in recomputation mode: content checks off — the slice replays recorded IDs rather than fresh ones — and admission restricted to inference packets.

Release, however, is **gated on the slice commitment**. The interlock stages the whole challenge, checks it is bit-identical to what the EXP marker promised, and only then hands it on. An incomplete, reordered or altered challenge therefore never reaches the enclosure, closing the covert channel a partially-staged challenge would open between the prover frontend and the recomputation enclosure.

Note: An armed expectation is not consumed by the release, but it cannot open a second bucket either: every packet carries `BUCKET` in its header, so the same payloads staged a bucket later hash differently and cannot reproduce the armed digest. An empty bucket can repeat a digest, but an empty bank is never drained.

A released bucket is then **retained** until the challenge dispatches `Û`, so a challenge-level retry can replay the same bytes from the buffer.

## Recomputation loop

```
TODO handle drop flag from estimate eth_deframe
```
`recomp_feed` takes the bucket's first packet as the challenged response — capturing its payload and forwarding only a sanitized, payload-less header — then forwards the context verbatim and feeds the captured tokens back one at a time against the enclosure's estimates, accumulating `Û`. The estimate return path is port 1's deframe feeding the estimate port directly; its truncation flag is unused — a malformed estimate frame is charged `PROB_MIN` regardless.

## Attestation and egress

A single `cert_build` in recomputation mode, see verification-protocol.md for the field assignments. The frontend egress is prod's mux with the packet input tied off: certificates on the default grant, sync packets on the priority input, reframed toward port 0. The device keeps the production timeline machinery — buckets, sync, certificate tiling — by decision; the protocol pins this layout and moves the H1/H2 checks to the verifier.

## Shared bucket clock

A single free-running timer paces the whole device, configured by one parameter: `BKT_MS`, the bucket period in milliseconds, from which the tick period and the buckets-per-certificate count derive. Unlike production this core runs **one bucket per certificate** — `BKT_MS = 1000`, a 1 s bucket — which is what lets the release gate compare against a digest covering exactly the bucket it is deciding on. Its tick fans out to canon (bucket check, marker insertion, sync emission) and buffer (bank swap); every bucket index in the device derives from this one counter. The buffers' grace periods absorb upstream tail: 2000 cycles.
