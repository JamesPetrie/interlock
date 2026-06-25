# Production Interlock Canonical Packet Processing Block Design Specification

This document describes the **canonical packet processor** — the canonical layer of the path, one instance per direction (see the Interlock Core doc). Like `eth_deframe` one layer down, all knowledge of its layer's format lives here: it checks and verifies each canonical packet as it streams through verbatim, and signals the result on its output stream — total length on `tuser` at beat #0, drop flag on `tuser` at `tlast`, and bucket boundaries as inserted empty transactions — leaving staging and batching to the downstream.

```
                       ┌──────────────────────────────┐
  AXIS in ───────────▶ │                              │ ───────────▶ AXIS out
  tuser:               │          canon_proc          │ tuser:
   drop @ tlast        │                              │  len @ beat #0
                       └──────────────────────────────┘ drop @ tlast
```

The processor sees only the Ethernet DATA field, already de-framed (including the removal of PAD bytes).

## Header capture — shift register

Incoming bytes stream into a shift register that is exactly one header deep (`HDR_BYTES`). Once the full header has shifted in, the header **checks** run on the parsed fields.

`PLD_LEN` is where the packet's **total length** comes from: `total = HDR_BYTES + PLD_LEN`, known as soon as the header is complete — before any payload arrives. This is the value driven on `tuser` at beat #0.

The packet passes through **verbatim**: header and payload stream out of the register unchanged — there is no separate header storage. Unlike the Ethernet header, whose fields are forced to fixed/derived values, the canonical header itself carries crucial information, so it passes through unchanged once checked.

## Validity checks

Checks run on each packet as it streams through, and the verdict is available at the packet boundary, where it is signalled downstream rather than acted on locally:

- a **header-check failure** is known before the packet's first byte leaves the register, so the processor simply **suppresses the packet's emission** — nothing reaches the buffer:
  - `ID` validity check. (ID 0/1 are reserved for control packets)
  - `ID` monotonity check.
  - `PLD_LEN` range check (must fit the maximum canonical payload).
  - `REFERENCE` validity check (is less than current ID)
  - `BUCKET` match check
  - `RESERVED` value check
- a **payload-check failure** is only known at end-of-packet, so it is signalled as the **drop flag on `tuser` at `tlast`**:
  - Payload length consistency check (number of payload bytes must match `PLD_LEN`).

Dropped packets are simply dropped — not counted, not escalated; recovery, if needed, is the endpoints' job via a new request.

## Nonce capture

One reserved transaction carries a **nonce** rather than a normal request: the packet whose `ID` is zero. It fails the ordinary `ID` validity check, so it is **suppressed** like any other header-check failure — it never reaches the buffer — but its `KEY_COMMIT` field is latched into the `nonce` register and driven continuously on the `nonce` output until the next nonce packet replaces it.

Only the low `CANON_NONCE_W` bits of `KEY_COMMIT` are kept. The capture is gated on a **non-fractional** header: an `ID == 0` match is only trusted when the header is a single contiguous packet, so a fractional or spliced header window can never latch a nonce.

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
