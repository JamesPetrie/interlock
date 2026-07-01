# crypto/ — swappable SHA-256 / HMAC fabric implementation

This directory holds the **reference inferred-fabric** SHA-256 and HMAC-SHA-256 cores used by `traffic_commit` (the commitment block, one level up in `src_hdl/`). It is deliberately isolated so the crypto backend can be swapped without touching the commitment logic — for production the intended target is the MPF300TS hardware crypto block (`PF_USER_CRYPTO`, see the repo README), or an inferred-fabric option such as OpenTitan's `hmac` IP. Until then this fabric implementation lets the whole path simulate.

## Files

- `sha256_pkg.sv` — SHA-256 constants (H₀, K), the round / message-schedule functions, and the big-endian↔AXIS `swap32` helper.
- `sha256_core.sv` — iterative single-block compressor (`iv + block → digest`), one round per cycle. This is the unit the device hardware crypto block would replace.
- `sha256_msg.sv` — streams an arbitrary byte message through the core with FIPS 180-4 padding and multi-block chaining, one digest per `in_last`-delimited message. Auto-frames on `in_last` (no `start` strobe); `in_ready` stays high across message boundaries so a caller can gate unconditionally on it, and absorb/compress are decoupled (the absorb FSM assembles the next block while the core compresses the previous), so back-to-back messages run with no bubble between them. This is the plain-hash unit: the payload and overall hashes of the commitment path are each one instance (the header/packet hashes use `sha256_core` directly).
- `hmac_sha256.sv` — wraps a single `sha256_msg` to compute HMAC-SHA-256 (ipad/opad two-pass), injecting the pad blocks ahead of the caller's stream. It presents the *same* auto-framed interface as `sha256_msg` (plus a continuous `key` sideband), so the two are drop-in swappable. The two passes share one engine, so HMACs don't pipeline back-to-back the way plain hashes do.

## Swap boundary

The commitment stages depend only on this streamed-message-in, digest-out interface — `sha256_msg` for the payload/overall hashes and `hmac_sha256` (same ports + `key`) in `cert_build`:

```
[key,] in_valid/in_ready, in_data/in_bytes/in_last  ->  done, digest[255:0], len[31:0]
```

No other dependency on this directory (each stage's `swap32` is imported from `sha256_pkg`). To swap the backend, provide a module exposing the same interface (an adapter over `PF_USER_CRYPTO`'s command/DMA path, or over OpenTitan's TL-UL FIFO) and re-point the stage's instantiation. The cores here can stay as the cocotb golden-model oracle.

## Tests

`tb/test_sha256_core`, `tb/test_sha256_msg`, `tb/test_hmac_sha256` validate each layer bit-exactly against Python `hashlib` / `hmac`; `tb/test_traffic_commit` exercises the whole block. Run via `make test-sha256-core` / `-sha256-msg` / `-hmac-sha256` / `-traffic-commit` from the repo root.
