# Recomp Feed Block Design Specification

This document describes the **recomp feed** — the recomputation dataplane of
the [recomputation interlock core](recomp_ilock_core.md). It is the one block
where the two directions **couple**: it forwards the challenged input context
to the prover's recomputation compute, then feeds the scored response
**token-by-token**, receives the per-position **estimates** the compute
emits, checks each for normalization, accumulates each position's surprisal into
the running total `Û`, and hands off the result.

```
                        ┌───────────────────────────────────────┐
  AXIS in  ───────────▶ │              recomp_feed              │ ───────────▶ AXIS out
  tuser:                │                                       │ tuser:
   len @ beat #0        │  forward all but the final response   │  len @ beat#0
                        │                                       │
                        │                                       │
         Û ◀─────────── │  final response:                      │ ◀─────────── AXIS est
                        │   START ─▶ len,timing,tok_0 estimates │
                        │   loop: reveal tok_i ─▶ tok_i+1 est.  │
                        │   CLOSE                               │
                        │                                       │
                        └───────────────────────────────────────┘
```

## Forwarding

All packets except the challenged response are forwarded as-is. The challenged response is preceded by a CTRL packet indicating the start of the recomputation.

The challenged response however is captured into a buffer and fed to the recomputation cluster token-by-token:
1. Before feeding any tokens from the buffer, a CTRL packet is sent which triggers 3 estimation responses: length, timing and token_0.
2. Upon receiving the token_0 estimates, the block enters a loop, revealing the actual token for each estimate received.
3. The loop ends when the end of the payload is reached:
  a. the packet was not full and the estimate for token_N+1 arrives (expecting the special end-of-stream entry to have high probability)
  b. the packet was full and the estimate for token_N arrives (no point revealing token_N since token_N+1 is not in this packet)

  The final estimate is still scored in both cases — in (b) token_N is scored from the buffered token even though it is not revealed, so a response spanning multiple packets is scored packet-by-packet with no cross-packet token context.

## Timing estimate

The timing estimate predicts the bucket difference between the challenged response and the corresponding request. The original bucket numbers might be overridden in the packet headers, in which case the original difference must be supplied in the response packet's header (e.g. replacing the ID value).

## Frame formats

**Egress**

A reveal is a single `(index, token)` pair, no header.

Forwarded **context** packets are not reframed by the feed — they pass
through verbatim with only the beat-#0 length carried on `tuser`.

**Ingress**

An estimate is a list of `(value, probability)` pairs, no header:

| Entry | Meaning |
|---|---|
| 0          | EOS — probability of the response ending at the given index |
| 1 … N-1    | `(value, probability)` pairs |
| N          | catch-all — probability of "any other value" |

Entry #0 (EOS) is meaningful only for token estimates; the length and timing estimates carry ordinary `(value, probability)` entries plus the entry-#N catch-all over their own value spaces.

## Scoring

There is a tradeoff between using raw probabilities p vs surprisals log(p) on the wire. Normalization checking is easier with probabilities while surprisals avoid rounding errors being introduced. As a trade-off, we use probability represented as a floating point number (parameterizable custom format) which is somewhere between. It allows normalization on a wide accumulator using a barrel shifter while directly expressing the mantissa for iterative logarithm approximation:

1. **Normalization check.** The estimate must describe a valid
   sub-distribution — its total mass (candidates plus the catch-all) is
   `≤ 1`. This stops the compute from assigning full weight to everything and
   scoring nothing. On **failure** the estimate is **dropped** and the
   position is charged the **maximum entropy** of its value space (uniform
   over all values — e.g. the full token width for a `TOKEN`). No fault, no
   abort: a malformed estimate costs the prover a full-entropy position and
   the loop continues.

2. **Accumulate.** Take the probability the estimate assigns the **actual
   value** (the catch-all's probability if that value is not explicitly
   listed), convert it to a surprisal (`−log₂`), and add it to `Û`.

`Û` — the total over length, timing, and every token position — is the
block's output.

**Why both length and per-position EOS.** They guard opposite directions. A
*response longer than the model would generate* is caught online by the
per-position EOS: the model bets mass on ending, the revealed token
contradicts it, and the token's reduced probability is charged. A *response
shorter than natural (premature termination — a covert channel)* can encode
more information than a single token, but once the text is revealed, a single
special token can hint where the end is supposed to be. The length
estimate catches this because it is committed at START, before any token is
revealed, so it reflects the natural length expected from the prompt alone.
The two overlap a bit, but the overlap is negligible: after the full text
is revealed EOS should be as easy to predict as any other token, so the
terminal EOS surprisal is small and adds little on top of the length estimate.
