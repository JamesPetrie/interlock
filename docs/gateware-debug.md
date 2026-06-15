# On-silicon debug: UART telemetry (off the datapath)

Goal: rich on-chip status over UART so issues can be diagnosed at runtime,
**without** a build→flash→bisect cycle per question — while keeping **UART off
the datapath** (the cert/data path stays in fabric; UART is a read-only observer).

## Architecture — CPU-free (no soft CPU, no firmware), built + verified

```
 datapath signals ──(read-only taps)──▶ dbg_telemetry ──▶ telemetry_uart ──▶ uart_tx ──▶ host
                                         (counters,         (dumper FSM:        (8N1
                                          sticky flags,      walk regs ->        serial)
                                          probe mux)         ASCII hex)
```

All in fabric — **no MiV, no firmware** (smaller TCB, no SoftConsole/hex-rebake
loop, faster iteration), and **independent of the Ethernet datapath** so it keeps
reporting even when forwarding is broken (the failure we chase). The MiV *is*
currently in the build (for PHY/MDIO bring-up + the old MAC-stat firmware), but
telemetry doesn't need it; fully deleting the MiV is a follow-on once PHY init
moves to a fabric MDIO FSM.

- **`dbg_telemetry`** (`gateware/src/core/dbg_telemetry.v`, cocotb-verified):
  event **counters**, sticky **event flags** (write-1-to-clear), and a
  runtime-selectable **probe mux** — `probe_sel` repoints a 32-bit `probe_val` at
  any wired-in internal bus. Single clock, read-only taps; cannot perturb the datapath.
- **`telemetry_uart`** (dumper FSM, verified): sweeps the registers, drives
  `probe_sel` itself to dump every probe lane, emits ASCII-hex fields + CRLF.
- **`uart_tx`** (8N1, verified) → serial line on the FlashPro UART.
- **`telemetry_top`** wires the three; end-to-end cocotb test parses a sweep and
  confirms the reported counters/flags/probes.

(A MiV+firmware variant is also possible — APB-map `dbg_telemetry` and poll it —
if you later want an interactive command interface. Not needed for status dumps.)

**The one limit:** you can only observe what you wired in. So wire a *generous*
set into the counters/flags and a *wide* probe mux up front; then a rebuild is
needed only for a signal nobody anticipated. (The dumper emits a fixed format, so
changing the *format* needs a rebuild — but the probe mux already covers changing
*which signals* you see, by dumping all lanes every sweep.)

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

## Integration plan (CPU-free)

1. Instantiate `telemetry_top` in `top.tcl` with `DIV = sys_clk_hz / 115200`.
2. Wire the datapath taps (counters/flags/probe lanes above) into `evt` /
   `flag_set` / `probe_in`.
3. Route `txd` to the FlashPro UART pin (the pin `CoreUARTapb` drives today —
   repurpose it, or add a spare). No APB, no firmware.
4. Rebuild → a `deframe-fix` image **with telemetry**, so the first forward/cert
   test on silicon reports exactly where frames stop if it misbehaves. Read it on
   the host with `cat /dev/ttyUSB0` (115200) inside the flash VM.

Keep all debug RTL simple (plain counters/regs/mux, single clock, no packages or
multi-dim arrays) so it adds no synth-vs-sim risk of its own.
