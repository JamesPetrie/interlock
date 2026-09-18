# Production Interlock Core Design Specification

This document describes the **production interlock core** — the full-duplex bump-in-the-wire implementing the interlock device role of `verification-protocol.md`: canonical checking, traffic commitment, bucket buffering, and once-per-second attestation. It covers how the core is deployed and why the blocks compose the way they do; block internals live in their own docs.

```
                                                        ┌───────────┐
                                                        │   bucket  │
                                                        │   timer   │
                                                        └───────────┘
  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──  ┬  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──
Request direction                              outside region   inside region
                                                              │
        ┌──────────┐  ┌────────────┐    ┌────────────┐  ┌───── ─────┐  ┌────────────┐  ┌──────────┐
MAC0──▶ │   eth    │─▶│ canon proc │───▶│   traffic  │─▶│  buc│ket  │─▶│  2×1 mux   │─▶│   eth    │──▶ MAC1
FIFO    │ deframe  │  │ + drop*    │    │   commit   │  │  buf fer  │  │            │  │ reframe  │    FIFO
        └──────────┘  └─────┬─┬────┘    └──────┬─────┘  └─────│─────┘  └────────────┘  └──────────┘
tuser:          drop@tlast  │ │   len@beat#0   │   len@beat#0                ▲    len@beat#0
                            │ │   swap(inline) │ swap(inline) │              │
                            │ │                │                             │
                            │ └────────────┐   │ traffic      │              │
                           s│     nonce    ▼   ▼ digest                     s│
                           y│            ┌───────────┐        │             y│
                           n│         ┌──│   cert    │                      n│
                           c│         │  │   build   │        │             c│
                            │         │  └───────────┘                       │
                            │         │        ▲ traffic      │              │
Response direction          │         │        │ digest                      │
                            ▼         │        │              │              │
        ┌──────────┐  ┌────────────┐◀─┘ ┌──────┴─────┐  ┌───────────┐  ┌─────┴──────┐  ┌──────────┐
MAC0◀── │   eth    │◀─│  3×1 mux   │◀───│   traffic  │◀─│  buc│ket  │◀─│ canon proc │◀─│   eth    │◀── MAC1
FIFO    │ reframe  │  │            │    │   commit   │  │  buf fer  │  │            │  │ deframe  │    FIFO
        └──────────┘  └────────────┘    └────────────┘  └─────│─────┘  └────────────┘  └──────────┘
tuser:           len@beat#0       len@beat#0       len@beat#0      len@beat#0     drop@tlast
                                                 swap(inline) │    drop@tlast
                                                                  swap(inline)

* drop: a separate axis_pkt_gate after canon proc
```

## Deployment — bump in the wire

The core sits between two CoreTSE MAC client interfaces on a single fabric clock (the M\*CLK domain — the MAC FIFOs handle the line-side CDC). Port 0 faces the **client** (prover frontend, the outside region), port 1 the **server** (prover compute, the inside region).ß

The endpoints' side of the contract:

- speak **canonical packets**, one per Ethernet frame (a packet must fit the frame's DATA field);
- stamp each packet with the **current bucket**, tracked from the sync packets arriving on the sender's own ingress wire (`FIRST_ARR` calibration — see the canon doc);
- keep **IDs monotonic** (per session inbound, per bucket outbound — the prover pre-sorts);
- inject the verifier's **nonce** as the reserved `ID = 0` control packet on the request path;
- leave room for the spliced-in streams — **certificates** on the response egress, **sync packets** on both — by reserving their bandwidth on the fill side (see below);
- treat drops as silent — there is no error path by design; recovery is a new request after timeout.

## Region split — commit on the outside edge

The dashed boundary in the diagram is the organizing decision: the **bucket buffer is the only block that crosses it**; every other block sits on one edge. In particular, both `traffic_commit` instances sit on the **outside** edge — requests are committed at **arrival** (before buffering), responses at **egress** (after buffering) — so INWARD and OUTWARD both describe the byte stream **on the external wire**, the stream the verifier can observe and challenge. This is why the two directions order their blocks differently.

The asymmetry cascades into the drop handling:

- **Request side** — `canon → gate → commit → buffer`. The commitment precedes the buffer, and it commits exactly what it forwards, so a payload-flagged packet must be squashed *before* it: that is the sole purpose of the separate `axis_pkt_gate`, which consumes canon's drop flag.
- **Response side** — `canon → buffer → commit`. No gate: the buffer's commit/abandon consumes the drop flag natively, and the commitment then sees only verified drained records, so the digest matches what actually left toward the wire.

## Inline Bucket marker flow and tuser

Each direction's bucket marker originates in its `canon_proc` and must reach every downstream block that groups by bucket, then stop. The per-instance `OUTPUT_SWAP` values encode exactly that.

`tuser` along the whole path is the total length at beat #0; the drop flag exists only on the one hop out of each `canon_proc`.

## Shared bucket clock

A single free-running timer paces the whole device: `TIMER_END = 79_999` → a 1 ms bucket at the 80 MHz fabric clock, `BKTS_PER_CERT = 1000` → one certificate per second. (REVISIT: the RTL currently carries a testing override — `TIMER_END = 7_999_999`, `BKTS_PER_CERT = 10` → 100 ms buckets, same 1 s certificate. The 320 KB buffer banks are sized for the 1 ms bucket, so test traffic must be rate-limited to under ~320 KB per direction per bucket (~26 Mb/s sustained); beyond that the bank abandons records — and on the request side commit precedes the buffer, so the abandoned packets are already in INWARD and the certificate stops matching the forwarded stream.) Its tick fans out to both canons (bucket check, marker insertion, sync emission) and both buffers (drain retarget); every bucket index in the device — both directions' checks, both digests' grouping, the certificate tiling — derives from this one counter, which is what makes a single certificate meaningful across both directions. The buffers' grace periods absorb each side's upstream tail: 2000 cycles on the request side (the gate and commit lengthen the path), 1000 on the response side.

## Control paths

- **Nonce** — the request-side canon latches `KEY_COMMIT` of the `ID = 0` packet and drives `cert_build.in_nonce` continuously; the response-side canon's nonce output is unused (no `KEY_COMMIT` in that header).
- **Sync** — each canon's sync packet is spliced into the **opposite** direction's egress mux: feedback toward that direction's sender. The request egress carries {requests, response-side sync}; the response egress carries {responses, certificate, request-side sync}.
- **Certificate** — a message pairs the two commits' hash pulses with the latched nonce and emits toward the client on the response egress.

## Sync packet and Certificate egress — bandwidth reservation

The certificate interleaves onto the wire stream. The mux can never *create* a gap in a saturated packet stream: if real packets fill every slot, there is nowhere to splice the cert. The reservation is therefore made **upstream, on the fill side**: the source periodically inserts gaps or frames deliberately constructed for dropping; the fill side discards them via checks already in place, leaving a hole in the drained stream.
