# On-silicon debug: UART telemetry (off the datapath)

Goal: rich on-chip status over UART so issues can be diagnosed at runtime,
**without** a build→flash→bisect cycle per question — while keeping **UART off
the datapath** (the cert/data path stays in fabric; UART is a read-only observer).

## Architecture

```
 datapath signals ──(read-only taps)──▶ dbg_telemetry ──APB──▶ MiV ──UART──▶ host
                                         (counters,                (poll + print
                                          sticky flags,             + command loop)
                                          probe mux)
```

- **`dbg_telemetry`** (`gateware/src/core/dbg_telemetry.v`, cocotb-verified):
  event **counters**, sticky **event flags** (write-1-to-clear), and a
  runtime-selectable **probe mux** — `probe_sel` (written over UART→MiV→APB)
  repoints a 32-bit `probe_val` at any wired-in internal bus. Single clock,
  read-only taps; it cannot perturb the datapath.
- **MiV + CoreUARTapb** (already in the design): firmware polls the registers and
  prints a status line each loop, and reads UART input for **commands**
  (set `probe_sel`, clear sticky flags, change verbosity, dump a snapshot). Because
  behavior is command-driven, you change *what you observe* at runtime — no reflash.

**The one limit:** you can only observe what you wired in. So wire a *generous*
set into the counters/flags and a *wide* probe mux up front; then a rebuild is
needed only for a signal nobody anticipated.

## What to expose for the interlock datapath

- **Counters:** frames in p0/p1, `eth_deframe` packets out, `canon_proc`
  accepted/dropped (per reason: truncated, length-mismatch), `interlock_core`
  accepted/dropped per direction, certs emitted, `eth_reframe` frames out.
- **Sticky flags:** truncated frame, length-mismatch, capacity overflow, FIFO
  overflow, `interlock_core.tick_err`, SGMII RX error.
- **Probe-mux lanes (the high-value snapshots):** `eth_deframe.dbg_eth_len`
  (**the exact signal behind the silicon LENGTH bug** — would have been a one-line
  read instead of a multi-build bisection), last `request_id`, last bucket digest
  word, deframe/core/reframe FSM state nibbles, current bucket counter.

## Cert readout — in fabric, not UART

Per the datapath constraint: the 140-byte certificate is framed back out through
`eth_reframe` as its own frame (custom 802.3 LENGTH / ethertype) and captured by
the host raw-socket test. UART never carries the cert — it only reports status.

## Integration plan

1. Wrap `dbg_telemetry` in a small APB slave; hang it on the MiV's `CoreAPB3` bus
   in `top.tcl` (next to the existing UART/CoreTSE slaves), pick a base address.
2. Wire the datapath taps (counters/flags/probe lanes above) into it.
3. Extend `main.c`: poll + print the status line; add the UART command parser
   (`probe_sel`, clear, verbosity).
4. Rebuild → a `deframe-fix` image **with telemetry**, so the first forward/cert
   test on silicon reports exactly where frames stop if it misbehaves.

Keep all debug RTL simple (plain counters/regs/mux, single clock, no packages or
multi-dim arrays) so it adds no synth-vs-sim risk of its own.
