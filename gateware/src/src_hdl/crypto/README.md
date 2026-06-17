# crypto/ — swappable SHA-256 / HMAC fabric implementation

This directory holds the **reference inferred-fabric** SHA-256 and HMAC-SHA-256 cores used by `traffic_commit` (the commitment block, one level up in `src_hdl/`). It is deliberately isolated so the crypto backend can be swapped without touching the commitment logic — for production the intended target is the MPF300TS hardware crypto block (`PF_USER_CRYPTO`, see the repo README), or an inferred-fabric option such as OpenTitan's `hmac` IP. Until then this fabric implementation lets the whole path simulate.

## Files

- `sha256_pkg.sv` — SHA-256 constants (H₀, K), the round / message-schedule functions, and the big-endian↔AXIS `swap32` helper.
- `sha256_core.sv` — iterative single-block compressor (`iv + block → digest`), one round per cycle. This is the unit the device hardware crypto block would replace.
- `sha256_msg.sv` — streams an arbitrary byte message through the core with FIPS 180-4 padding and multi-block chaining. Auto-frames on `in_last` (no `start` strobe); absorb and compress are decoupled (the absorb FSM assembles the next block while the core compresses the previous), so back-to-back messages run with no bubble between them — the next message is taken while the previous is still finalising.
- `sha256_hash.sv` — a thin name over `sha256_msg` (one digest per `in_last`-delimited message). `in_ready` stays high across message boundaries, so a caller can gate unconditionally on it. The payload, bucket, and overall hashes of `traffic_commit` are each one instance (the packet hash uses `sha256_core` directly).
- `hmac_sha256.sv` — wraps `sha256_msg`: a transparent plain-SHA mode plus HMAC (ipad/opad two-pass).

## Swap boundary

The commitment stages depend only on these streamed-message-in, digest-out interfaces — `sha256_hash` (32-bit, auto-start) for the payload/packet/bucket/overall hashes and `hmac_sha256` (32-bit, mode bit) in `cert_build`:

```
start[, hmac_en], in_valid/in_ready, in_data/in_bytes/in_last  ->  done, digest[255:0]
```

No other dependency on this directory (each stage's `swap32` is imported from `sha256_pkg`). To swap the backend, provide a module exposing the same interface (an adapter over `PF_USER_CRYPTO`'s command/DMA path, or over OpenTitan's TL-UL FIFO) and re-point the stage's instantiation. The cores here can stay as the cocotb golden-model oracle.

## Tests

`tb/test_sha256_core`, `tb/test_sha256_msg`, `tb/test_hmac_sha256` validate each layer bit-exactly against Python `hashlib` / `hmac`; `tb/test_traffic_commit` exercises the whole block. Run via `make test-sha256-core` / `-sha256-msg` / `-hmac-sha256` / `-traffic-commit` from the repo root.
