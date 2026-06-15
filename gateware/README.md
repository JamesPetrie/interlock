# Interlock gateware Core

Gateware twin of `prototype/interlock.py` — hashes every packet, folds per
direction into a running bucket hash, folds buckets into a per-second window
hash, and emits the certificate. Built bottom-up and checked at every stage
against the Python reference (`prototype/wire.py` + `interlock.py`) via cocotb.
**The HMAC tag is deferred** — the Core emits the 108-byte certificate *body*;
the HMAC FSM (over the same SHA core) is the next stage and extends the body to
the full 140-byte certificate with no change to the conformance method.

## Status

| Stage | What | State |
|---|---|---|
| G0 | Vendor secworks SHA-256 core | ✅ green vs NIST + hashlib fuzz |
| G1 | `sha256_stream` (byte-stream → auto-pad → digest) | ✅ green vs hashlib (empty/boundaries/fuzz/gappy/reuse) |
| G2 | `pkt_record` (H(ct) → packet_hash → record) | ✅ green vs `wire.record()` |
| G3–G5 | `interlock_core` (drop rules, bucket+window fold, cert body) | ✅ 5 tests green; cert body byte-identical to the model |
| — | Verilator `--lint-only -Wall` | ✅ clean (catches synth-vs-sim issues) |
| G7 | HMAC FSM → full 140-byte cert | ⬜ next |
| G6 | FPGA integration + on-hardware gate | ⬜ scoped (see below) |

## Files

- `src/secworks/` — vendored SHA-256 core (BSD; see its README for provenance).
- `src/core/sha256_stream.v` — streaming SHA-256 wrapper (the keystone: byte-stream
  in, automatic padding, `init`/`next` sequencing, re-initializable for running contexts).
- `src/core/pkt_record.v` — per-packet record path (two `sha256_stream` instances).
- `src/core/interlock_core.v` — the Core: validity/drop rules, per-direction running
  bucket + window hashes, 108-byte cert body assembly. Port contract in the header.
- `tb/sim.py` — cocotb runner (Icarus). `tb/test_*.py` — the benches.

## Running the sims

```
python3 gateware/tb/sim.py sha256_core      # G0
python3 gateware/tb/sim.py sha256_stream    # G1
python3 gateware/tb/sim.py pkt_record       # G2
python3 gateware/tb/sim.py interlock_core   # G3-G5 (the full conformance suite)
```

Each bench imports the Python reference from `../../prototype` and checks the RTL
against the same bytes the real system uses. `SIMDBG=1` adds a state trace;
`COCOTB_TESTCASE=<name>` runs one test.

## G6 — FPGA integration plan (and the gap)

The Core consumes a **byte stream of `wire.py`-format packets**
(`s_valid/s_ready/s_data/s_last/s_dir`). The MPF300 path provides:

- The MAC (`CoreTSE`) speaks a 32-bit MRX/MTX FIFO interface (`*_mrx_rdy/acpt/
  sof/eof/dat[31:0]/bytevalid[1:0]`). The currently-flashed `fabric_bridge.sv` is
  the transparent passthrough (bisection build).
- `Libero_Project/hdl/eth_deframe.sv` already converts MRX → the ethernet payload
  as **32-bit AXI-Stream** (`tvalid/tready/tdata[31:0]/tkeep[3:0]/tlast/tuser`).

So integration needs, in order:

1. **Framing decision (design input needed).** The Core wants raw `wire.py`
   packets, but the prototype currently runs the protocol over TCP, so
   `eth_deframe`'s payload is IP/TCP + the interlock packet. Two options:
   (a) parse TCP payloads in fabric (heavy), or (b) **have the prototype send
   interlock packets as raw Ethernet frames with a custom ethertype** so the
   deframed payload *is* the `wire.py` packet (recommended for the prototype).
2. **Width adapter** `axis32 → byte`: 32-bit `tdata`/`tkeep`/`tlast` → the Core's
   8-bit `s_data`/`s_last`/`s_valid`. (`eth_deframe.sv` is the reference for the
   MRX/AXIS byte ordering — get `tkeep`/`bytevalid` right; this is the synth-vs-sim
   risk area, cf. the deframe LENGTH bug.)
3. **Direction** `s_dir` from which port the packet arrived (port0 = request/"in",
   port1 = response/"out").
4. **`bucket_tick`** from a free-running timer (the 1 ms boundary). Boundaries take
   ~280 cycles, far below 1 ms, so the single `tick_pending` latch is safe.
5. **Cert readout** — expose the `cert_*` stream (e.g. to the MiV/UART for the
   demo, or inserted into a side channel). The on-hardware gate is: compare the
   Core's emitted cert body to the Python `interlock` on the same packets.

Build on the Spark (Libero), flash via FlashPro Express in the qemu VM, then run
the on-hardware comparison. **sim passing ≠ silicon** — keep the deframe
synth-vs-sim history in mind; the cert-byte comparison on the real device is the
final gate.
