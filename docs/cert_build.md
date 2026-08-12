# Certificate Builder Block Design Specification

This document describes the **certificate builder** — the top layer of the attestation path, instantiated **once per interlock** (the per-direction blocks feed it). Once per certificate period it takes both directions' `overall` digests plus the latched recency nonce, assembles the signed message `m`, computes `τ = HMAC-SHA256_k(m)`, and streams the certificate frame on a dedicated AXIS port. The certificate wire format is owned by **`verification-protocol.md`** — this document covers the block.

```
  overall_req  ─────────▶ ┌──────────────────┐
  overall_rsp  ─────────▶ │    cert_build    │ ──▶ AXIS cert out
  nonce ────────────────▶ │                  │     tuser: len @ beat #0
  key ──────────────────▶ └──────────────────┘
```

## Latch and pairing — RECOMP

Each `overall` input is a **pulse sink**: latched off its valid pulse, with no handshake — an `overall` arrives once per certificate period (~1 s), vastly longer than the HMAC plus frame drain, so the block is idle when each one lands. The nonce is sampled alongside.

`RECOMP` selects what fills the OUTWARD/RECOMP slot. `RECOMP = 0` (prod design): a certificate needs both directions — emission waits for one `overall_rsp` per `overall_req`, and both are consumed per certificate. `RECOMP = 1` (recomp design): there is no response digest at all. The request digest alone drives emission, and OUTWARD/RECOMP instead carries whether the INWARD bucket was released in the MSBit and the recomputation results in the lower bits. The recomputation result are sampled free-running like the nonce (zero before the first sample, stale between; pairing and validity semantics are the protocol layer's to define).

## Message, tag, frame

```
  m     = // see verification-protocol.md
  τ     = HMAC-SHA256_k( m )
  frame = [ reserved canonical header (all 0) ] ‖ m ‖ τ
```

The packed-struct order in the RTL **is** the wire order, all fields big-endian. `bucket_start` is a counter advanced by `NUM_BUCKETS` on every built certificate (the protocol's tiling / completeness rule), and `prev_tau` chains the previous certificate's τ into the next `m`.

## Frame flagging — reserved zero header

On the wire the record rides as an opaque canonical packet behind a **reserved all-zero prefix** of `HDR_BYTES`. Real traffic never uses canonical `id = 0` (`canon_proc` reserves IDs 0/1 for control), so the receiver reads `id = 0` where the canonical header's `id` field sits and parses the remainder as a certificate. The prefix is *reserved padding*, not a populated header — no other field of it is interpreted.

## Late link-up — dropped certificates (accepted limitation)

The "cert period ≫ HMAC + drain" premise assumes the egress drains; a down Ethernet link at boot violates it (PHY autonegotiation takes seconds while the fabric timer free-runs from reset). If the next period's `h_done` fires while the frame serializer still holds a wire-stalled certificate, that frame is silently dropped but `bkt_start` and `prev_tau_q` still advance, leaving a `bucket_start` hole.

**Decision:** drops are accepted; traffic is held until links are up, so dropped windows are traffic-free and the verifier reconstructs them — cert 0 always survives (first-in wins the stalled serializer) and anchors the chain, each dropped cert's `m` is fully deterministic (`overall = SHA256(SHA256("") × NUM_BUCKETS)` per direction), and the next delivered cert's `prev_tau` checks the reconstruction.

Load-bearing invariant: `prev_tau_q` and `bkt_start` advance on **every** `h_done`, including dropped frames — **emission is best-effort, accounting is not**.

## Placeholders / open items

- **Hardened Crypto core.** The design is currently using a crypto core for the signature implemented in the fabic. It should eventually switch to a hardened block in the FPGA chip.
- **Reset anchor.** We might need some non-volatile memory to track run sessions.
