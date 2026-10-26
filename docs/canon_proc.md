# Canonical Packet Processing Block Design Specification

This document describes the **canonical packet processor** — the canonical layer of the path. Like `eth_deframe` one layer down, all knowledge of its layer's format lives here: it checks and verifies each canonical packet as it streams through verbatim, and either masks (header failure) or signals a failure on its output stream (payload failure).

```
                       ┌──────────────────────────────┐
  AXIS in ───────────▶ │                              │ ───────────▶ AXIS out
  tuser:               │          canon_proc          │ swap (inline)
   drop @ tlast        │                              │ tuser:
                       └──────────────────────────────┘  len @ beat #0
                                                        drop @ tlast
```

The processor sees only the Ethernet DATA field, already de-framed (including the removal of PAD bytes).

The `DIR` parameter fixes which canonical header this instance parses — the **request** header or the **response** header — and is the `DIR` value the packets of that log carry on the wire. The recomputation instance does not have its own DIR value now; it uses `RECOMP` to disable the checks which are not needed there (it replays packets of both logs, so the `DIR` match is skipped too).

## Header capture — shift register

Incoming bytes stream into a shift register that is exactly one header deep (`HDR_BYTES`). Once the full header has shifted in, the header **checks** run on the parsed fields.

`PLD_LEN` is where the packet's **total length** comes from: `total = HDR_BYTES + PLD_LEN`, known as soon as the header is complete — before any payload arrives. This is the value driven on `tuser` at beat #0.

If header checks passed, the packet passes through **verbatim**: header and payload stream out of the register unchanged — there is no separate header/payload storage. Unlike the Ethernet header, whose fields are forced to fixed/derived values, the canonical header itself carries crucial information, so it passes through unchanged once checked.

## Validity checks

Where a failure is addressed depends on which check failing:

- a **header-check failure** (integrity or content) is known before the packet's first byte leaves the register, so the processor **suppresses the packet's emission**. Checks split into two classes by the `RECOMP` parameter (off by default, so content checks run).
  - **Integrity checks** — always on; that the beats form a well-formed canonical packet:
    - **full-header** check: the packet is long enough to carry a complete header (guards against a fractional or spliced header window).
    - `PLD_LEN` range: payload fits the maximum canonical payload (`PLD_MAX = CANON_PKT_BYTES_MAX − HDR_BYTES`).
    - `PLD_LEN` remainder: the payload is a whole number of token units (a multiple of `CANON_TOK_BYTES`).
    - `TYPE` is Traffic: control packets are never forwarded.
    - `DIR` match: the header's direction matches the direction of the pipeline.
    - `BUCKET` match: the header's bucket equals the interlock's current bucket.
    - `SLOT` capacity: `SLOT < CANON_NUM_SLOT`.
  - **Content checks** — skipped wholesale when `RECOMP` is set:
    - `SLOT` order: `SLOT >= next_slot`, one past the slot of the last packet that fully passed (header **and** payload). `next_slot` restarts at 0 at each bucket boundary.
    - `REFERENCE`: not later than the packet — same `DIR`: `(BUCKET, SLOT)` not above the packet's own (a root packet references itself); other `DIR`: a strictly earlier `BUCKET`.
    - `RESERVED` value: the reserved field is all-zero.
- a **payload-check failure** is only known at end-of-packet, so it is signalled as the **drop flag on `tuser` at `tlast`**, on either:
  - a length mismatch — received payload byte count != `PLD_LEN`; or
  - the upstream truncation flag (input `tuser` at `tlast`, from `eth_deframe`), carried through with the beat.

Dropped packets are simply dropped — not counted, not escalated; recovery, if needed, is the endpoints' job via a new request.

## Nonce capture

A nonce packet carries the **nonce** in its `KEY_COMMIT` field. That field lives only in the request header, so this requires `DIR = CANON_DIR_REQ` (the recomp ingress uses REQ for exactly this reason). The low `CANON_NONCE_W` bits of `KEY_COMMIT` are latched into the `nonce` register and driven continuously on the `nonce` output until the next nonce packet replaces it.

Note: the packet itself always fails the `TYPE` check and is **suppressed** on the output.

## Timer synchronization — sync packets

The `BUCKET` match check only admits packets stamped with the interlock's current bucket, so the sender must track the interlock's bucket clock. Each `canon_proc` therefore emits a **sync packet** on a dedicated AXIS master at every tick, to be routed back toward its direction's sender.

The packet format is defined in `verification-protocol.md` (*Control packets*).

`DELTA` is the calibration feedback; the header's `SLOT` identifies the packet it timed. The sender first observes only the tick cadence and aims a single probe at the middle of a bucket; the closing sync packet tells it how deep into the bucket the probe actually landed, and it widens the window it targets around that estimate — feedback again, widen again — iterating out to the usable range. One timing field suffices: a sender that fills its window at line rate knows where its last byte lands relative to its first.

"Accepted" means the packet passed the header checks — the same instant its bucket membership is decided. A packet that later raises the payload drop flag still counts. The tracking re-arms when the bucket-boundary marker is emitted.

## Pipelining — back-to-back packets

The shift register never needs to drain to idle between packets: when the last byte of packet *N* enters the register, the final `HDR_BYTES` of its payload are still inside it, streaming out toward the buffer. Packet *N+1*'s header bytes can begin shifting in immediately behind them. Header capture for the new packet and payload write-out for the old one overlap, so the pipeline sustains back-to-back packets with no inter-packet gap.

```
  time ──▶
  reg in :  … N payload tail │ N+1 header │ N+1 ……………………………… payload …
  reg out:  … N payload ……………………………… tail │ N+1 header | N+1 payload …
                                          ▲
                           N+1 checks run here when the full
                           header is in the register.
```

When no next packet is waiting, the register still drains: once a packet's `tlast` has entered the tail, empty **bubbles** shift in behind it, pushing its remaining beats out to the head so it completes without depending on the next packet. A bubble never enters behind an *unfinished* packet, so a packet's beats stay contiguous and the header parse positions are fixed.

## Bucket boundaries

The path is partitioned into **buckets** by a shared `timer` (see the Interlock Core doc): a tick closes the current bucket and opens the next. The `BUCKET` match check above keys off the same tick, so the input side moves to the new bucket the instant the timer fires — independent of any downstream backpressure — and the processor signals the matching boundary on its output stream so the downstream groups packets the same way.

The boundary is signalled **out of band**, as a single empty beat — `tkeep = 0`, `tlast` asserted, `tuser` zero — inserted between packets. A real packet never emits a zero-keep beat, so the downstream identifies the marker unambiguously by `tkeep == 0`, advances its bucket, and drops the payload-less beat.

The marker is only ever inserted at a **packet boundary**, never mid-packet. A tick that arrives mid-packet rides out behind that packet — which belongs to the closing bucket — while a tick in an inter-packet gap is inserted as soon as the output is free. Insertion borrows the output slot for one beat, so it costs at most a single-cycle bubble, and only when the next packet is already waiting back-to-back.

```
  out:  … packet N+1 (BKT=M+1) header │ empty │ packet N (BKT=M) payload …
                                      ▲
              bucket boundary — one tkeep = 0 beat,
              inserted between packets on a timer tick.
```
