# Recomp Feed Block Design Specification

This document describes the **recomp feed** — the recomputation dataplane of
the [recomputation interlock core](recomp_ilock_core.md). It is the one block
where the two directions **couple**: it forwards the challenged input context
to the prover's recomputation compute, then feeds the scored response
**token-by-token**, receives the per-position **estimates** the compute
emits, checks each for normalization, accumulates each position's surprisal into
the running total `Û`, and hands off the result — the challenged response's
`ID` paired with `Û`, the pair the recomp certificate carries in OUTWARD.

```
                        ┌───────────────────────────────────────┐
  AXIS in  ───────────▶ │              recomp_feed              │ ───────────▶ AXIS out
  swap (inline)         │                                       │ tuser:
  tuser:                │  forward the context verbatim         │  len @ beat#0
   len @ beat #0        │                                       │
                        │                                       │
     id, Û ◀─────────── │  challenged response (bucket's 1st):  │ ◀─────────── AXIS est
                        │   swap ───▶ len,timing,tok_0 estimates│
                        │   loop: reveal tok_i ─▶ tok_i+1 est.  │
                        │                                       │
                        │                                       │
                        └───────────────────────────────────────┘
```

## Forwarding

All packets except the challenged response are forwarded as-is. The challenged response is the first packet after the SWAP beat.

The challenged response's header however, gets sanitized to be identifiable and to prevent providing hints to the recomputation enclosure. It's tokens are not forwarded immediately either, but captured into a buffer and fed to the recomputation cluster token-by-token after the context:
1. The closing swap beat ends the context, which triggers the expectation of 3 estimation responses: length, timing and token_0.
2. Upon receiving the token_0 estimates, the block enters a loop, revealing the actual token for each estimate received.
3. The loop ends when the end of the payload is reached and the estimate for token_N arrives (no point revealing token_N since there's no token_N+1 to predict)

Note: The final estimate is scored even if the packet is full (a response potentially continued in a later packet). Recomputation always scores a single packet.

While a challenge's estimate loop runs, the packet port **stays ready and drops everything whole** — never forwarded — so the timer-driven `batch_buffer` drain is never stalled across a challenge of arbitrary duration. The staging contract already keeps traffic out of an active challenge; the drop makes a violation degrade to lost packets — already committed upstream, so the digest exposes them — instead of corrupted framing. Once the challenge completes, the block resynchronises on the next **swap beat**, not merely at a packet boundary: arming is positional, so resuming mid-bucket could take a context packet for a challenged response. Little should arrive to drop in the first place — a bucket whose commitment does not match the expected digest never leaves the buffer — but the drop keeps the block sane if one is released anyway.

## Challenge retry mechanisms

Packet loss on the inference cluster's network is not catastrophic. The gatewey can set up a timeout for each request and re-try if no response arrived in time.

The same is not true for the recomputation by default. An armed recomputation state waits for each estimate-reveal interaction to happen before reseting to idle state. So a lost packet there could deadlock the mechanism (e.g. a lost interlock reveal packet puts the interlock and the cluster out of sync both waiting for the other).

To resolve such cross-dependencies, we introduce two types of timeout and retry mechanisms:
- Challenge level retry: before any reveal happened, the challenge can be aborted and retried from start (re-feeding the whole sequence from the start).
- Reveal level retry: after the first reveal, the challenge is no longer retryable (the prover learned information about the output already). From this point a timeout on an expected estimate results in the interlock re-sending the last reveal (token index and value).

Notes:
- The recomputation cluster should not re-send any packet without a prompt from the interlock.
- The above mechanisms don't protect against a timed out packet still arriving at the end. The timeout values must be set such that they imply lost packets.

## Timing estimate

The timing estimate predicts the bucket difference between the challenged response and the corresponding request. The original bucket numbers might be overridden in the packet headers, in which case the original difference must be supplied in the response packet's header (the low word of the RESERVED field).

## Frame formats

The reveal and estimate frame formats are owned by `verification-protocol.md` (*Recomputation challenge frames*). Note: the current RTL still emits the bare token in the reveal frame; the `(index, token)` frame is pending on the recomp feature branch.

Forwarded **context** packets are not reframed by the feed — they pass
through verbatim with only the beat-#0 length carried on `tuser`.

The **challenged** packet's header is sanitized and forwarded without the payload.

## Scoring

There is a tradeoff between using raw probabilities p vs surprisals log(p) on the wire. Normalization checking is easier with probabilities while surprisals avoid rounding errors being introduced. As a trade-off, we use probability represented as a floating point number (parameterizable custom format) which is somewhere between. It allows normalization on a wide accumulator using a barrel shifter while directly expressing the mantissa for iterative logarithm approximation:

1. **Normalization check.** The estimate must describe a valid
   sub-distribution — its total mass (candidates plus the catch-all) is
   `≤ 1`. The catch-all is a *per-value* probability, so its mass is charged
   at `p × 2^W` (`W` = the value-space width in bits): in the exponent-based
   float this is pure exponent arithmetic, no multiplier. (REVISIT: `2^W` is
   an upper bound for the exact `2^W − K` unlisted values — sound, never
   under-counts, over-counts by `≤ K/2^W ≈ 4e-8`; an exact count would need
   a multiplier.) This stops the compute from assigning full weight to
   everything and scoring nothing. On **failure** the estimate is
   **dropped** and the position is charged `PROB_MIN`, the smallest
   representable probability — at least the value space's max entropy, so a
   malformed estimate never under-charges. No fault, no abort: the loop
   continues. A frame ending on a dangling value word (odd word count) is
   handled uniformly: that word is treated as a bare catch-all probability —
   normalized at `× 2^W` and scored as the fallback — so a truncated frame
   cannot dodge its charge either.

2. **Accumulate.** Take the probability the estimate assigns the **actual
   value** (the catch-all's probability if that value is not explicitly
   listed), convert it to a surprisal (`−log₂`), and add it to `Û`.

`Û` — the total over length, timing, and every token position — is
dispatched as a single-cycle pulse together with the challenged response's
`ID` as the block's output.
