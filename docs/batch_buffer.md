# Batch Buffer Block Design Specification

This document describes the **batch buffer** — a format-agnostic, ping-pong packet store. It stages verified packets as length-prefixed records in the filling bank, swaps banks at the bucket boundary, and re-emits each bucket toward the wire at its own pace. It knows nothing about the packet format.

```
                       ┌──────────────────────────────┐
  AXIS in ───────────▶ │         batch_buffer         │ ───────────▶ AXIS out
  swap (inline)        │        (ping-pong RAM)       │ swap (inline, if OUTPUT_SWAP == 1)
  tuser:               │                              │ tuser:
   len  @ beat #0      └──────────────────────────────┘  len @ beat #0
   drop @ tlast
```

## Staging — length-prefixed records

Each inbound packet is staged as a self-delimiting record: the **total length from `tuser` at beat #0 is injected as a 32-bit prefix word**, then the packet's words follow verbatim. `tkeep` is ignored — record geometry comes entirely from the prefix, which is what makes a bank parseable as a packet sequence with no knowledge of the content's format.

The buffer is **ping-pong**: two banks, one filling while the other drains, so ingest never contends with the consumer. The fill side is designed to be free-runing and allows finishing packets previously accepted upstream for the bucket. Whereas, the drain side waits for a grace period after the tick before reading the new bank so outstanding writes can finish.

Fill and drain run on the same fabric clock (the MAC FIFOs handle the line-side CDC), so each bank infers a simple dual-port BRAM with no synchronizers.

## Commit / abandon — write pointer

A record is written *speculatively* past the committed write pointer. At `tlast` it is either **committed** — the pointer advances over it — or **abandoned**, rewinding the pointer to the last committed value, when either:

- the **drop flag** (input `tuser` bit 0 at `tlast`) is set — the payload-failure verdict from `canon_proc`; or
- the record ran into the **reserved tail** of the bank (one max entry, guarding against overfill).

A bank therefore only ever holds verified, whole records: nothing unapproved crosses the swap, and no verdict metadata needs to.

## Bank swap — marker on the fill side, tick on the drain side

The two sides swap independently.

The **fill side swaps when the upstream bucket marker arrives** (the standalone empty beat). The marker only ever sits between packets, so no record straddles banks; the committed pointer freezes as the drain limit at that instant.

The **drain side invalidates the bank selector at the tick** and starts walking the new bank only after `GRACE_PERIOD` cycles, which must cover the upstream tail. By drain start the marker must have arrived and frozen the bank.

The grace period must be configured such that any packet already accepted upstream for the old bucket is guaranteed to finish writing within that period. To still sustain line rate, a downstream FIFO must be able to hold packets drained from the previous bucket for this short period.

## Drain — pure emission

The drain walker gets a frozen bank and limit, and re-emits each record as an AXIS packet via the length prefixes — `tuser` = total length on beat #0, straight from the prefix.

The bucket boundary is signalled on the output by the re-inserted **standalone empty swap beat** when `OUTPUT_SWAP = 1`: it follows the bucket's final record, and an empty bucket still emits it (once the first tick has passed). With `OUTPUT_SWAP = 0` the boundary is not signalled at all.

The drain's deadline is the flip side of the ping-pong: the fill side takes the bank back at the next tick, so a bucket must be **fully emitted within one bucket period**. Accepting it in time is the **downstream's** responsibility — the block has no preemption or truncation path, and a drain still running at the tick is UNDEFINED behavior. Concretely the sink must absorb the drain's head start: the grace period delays the drain by `GRACE_PERIOD` cycles while ingest ran at line rate, so it must buffer that much of a bucket. Significant backpressure (PAUSE frames, link loss) breaks the contract.

## Release gate and bucket retention

Two optional controls let the consumer pace the drain. Both are inert with the gate held open and the bucket acknowledged every cycle, which is the default tie-off configuration.

- **Release gate** (`rd_gate_en`, latched on `rd_gate_en_valid`): a bucket drains only while the gate is enabled.
- **Bucket retention** (`bkt_ack`, `bkt_replay`): a drain start **locks** the bucket. While locked the banks stop swapping and the fill side discards each period's traffic in place, preserving the drained bucket and its limit; `bkt_replay` re-walks it identically at the next grace boundary. `bkt_ack` clears the lock and normal ping-pong resumes.

The lock changes state only **at a tick** — an ack arriving mid-period is latched and consumed there — so the fill and drain bank selectors stay in lockstep whenever the ack lands. A locked period still emits its trailing swap beat under `OUTPUT_SWAP = 1`, so the boundary cadence on the output is unbroken whether or not a bucket drained.

## Sizing

Each bank must absorb continuous line-rate transfer for a full bucket period plus one prefix word per record, with the reserved one-max-entry tail on top — so the fill side never stalls mid-bucket, and an overrunning record degrades to an abandoned record rather than wedging the bank. The default is **320 KB per bank** (enough for ~2.5 ms of full 1 GbE — generous against the 1 ms production bucket), 640 KB per direction, all in on-chip LSRAM — no external memory in the data path.

## Open items
-  The module and this doc say *batch*; the rest of the path now calls the drain group a *bucket*.
