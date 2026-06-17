# eth_sanitize hardware bring-up: findings (2026-06-17)

Test of `feature/eth_sanitize` @ `bafc04e` ("add ethernet TYPE packet drop
feature") on the MPF300-EVAL-KIT (Spark `spark-c191`). Built on Hetzner, flashed +
tested on the Spark. Written for James + Peter to review.

**Bottom line:** The hardware is fully healthy and the forwarding + TYPE-drop
feature works **on silicon** — but it works with the **known-good prod-build
design**, not with the `eth_sanitize` build. The `eth_sanitize` bitstream flashes
fine (PROGRAM PASSED) but the **soft-CPU (Mi-V) never runs**, so the PHYs are
never configured and **no ethernet link ever comes up**. Firmware and all Mi-V
infrastructure in `eth_sanitize` are byte-identical to the working ancestor, so
the regression is in the `fabric_bridge` integration, not the firmware.

---

## 1. What works (PROVEN on silicon)

All of the following were verified with the **known-good prod-build** design
(`debug/uart-fabric-readback`, `.job` sha `1231a06f…`) flashed to the board:

| Check | Result |
|---|---|
| Flash (FPExpress over JTAG) | **PROGRAM PASSED** |
| Both ethernet links | **up immediately** (`carrier builtin=1 usb=1` at t=1s) |
| Mi-V firmware running | **yes** — UART `ttyUSB0`: `[poll t=127] … (link bits: 1111)` |
| Forwarding, MAC→Spark (`enP7s7`→USB) | **works** — 100 sanitized (forced-DST `02:..:01/02`) frames, payload intact |
| Forwarding, Spark→MAC (USB→`enP7s7`) | **works** — 100 sanitized frames |
| TYPE-drop (`type_drop.sh`) | **PASS** — LENGTH frames forward (300), TYPE `0x86DD` dropped (**0**), LENGTH still forwards after (300, no wedge) |

So: **the board, both NICs, both Ethernet cables, the FlashPro JTAG path, and the
FlashPro UART path are all fine.** The cabling James was worried about is fine —
with the correct design the links come up instantly. And the **forwarding +
TYPE-frame-drop feature is validated working on silicon** (via prod-build's
`reject L/T>1500` implementation, which is functionally the same as Peter's
`len_valid`/`beats_total=0` drop).

> Note: prod-build's forwarding/TYPE-drop is a *different implementation* of the
> same feature, so this validates the **feature/approach**, not Peter's exact RTL.
> Peter's RTL could not be tested because his build never reaches a running state.

---

## 2. What does NOT work: the eth_sanitize build

Flashing `eth_sanitize` (`.job` sha `c0530c05…`, PROGRAM PASSED):

| Check | Result |
|---|---|
| Flash | PROGRAM PASSED |
| Board powered / fabric configured | yes — "lots of LEDs on" (fabric/status LEDs) |
| The two **ethernet** LEDs | **off the whole time** (no SGMII link) |
| Both NIC carriers | **0** (no link), before and after a power-cycle, after reseating both cables |
| Mi-V UART (all 4 `ttyUSB` channels, 22s) | **completely silent** |

Same board, same cables, same read method that prints cleanly on prod-build →
**eth_sanitize's Mi-V genuinely is not running.** With the Mi-V dead, it never
runs `configure_zl30364()` / `tse_init()` / the PHY autoneg, so no PHY links, so
no forwarding is even possible. The "lots of lights" are fabric LEDs that run
without the soft-CPU; the ethernet-specific LEDs being off is the tell.

---

## 3. Differences between the three builds

Common ancestor: `5b1fa1d` "Merge feature/coretse-wrapper: dual-CoreTSE bridge
working **end-to-end**" — i.e. at the ancestor the firmware DID boot and bring up
both ports.

| Item | ancestor `5b1fa1d` | `eth_sanitize` `bafc04e` | prod-build `debug/uart-fabric-readback` |
|---|---|---|---|
| Mi-V firmware (`iog_cdr.hex`, `main.c`) | working | **byte-identical to ancestor** | reworked (`[dbg]`/RMON telemetry) |
| `MIV_RV32_C0.tcl`, `CoreAPB3_0.tcl`, `RAM.cfg` (firmware→TCM init) | — | **identical to ancestor** | changed (extra APB slave for dbg) |
| Data path | direct MAC→MAC wire | **`fabric_bridge` inserted** | `fabric_bridge` inserted |
| `eth_deframe` | n/a | header shift-reg + struct-cast `eth_hdr_from_bytes`; **no in-band `tlen`**; no instrumentation | **reworked**: in-band `tlen` sampled at the `tvalid` handshake (synth-vs-sim fix `498e025`), `tuser` registered locally (`e33345a`), reject `L/T>1500` + zero-fill, instrumentation taps |
| Interlock cores (sha256/hmac/cert egress) | no | no | yes (not relevant to forwarding) |
| Runs on silicon? | yes (per ancestor) | **NO** (Mi-V dead) | **yes** |

Key point: **`eth_sanitize`'s firmware and entire Mi-V/APB/init configuration are
byte-identical to the working ancestor.** The only substantive design change from
the ancestor is the `fabric_bridge` insertion (+ the eth RTL it pulls in).

---

## 4. Root cause

**PROVEN:** `eth_sanitize` does not run the Mi-V on silicon (silent UART on all
channels vs. prod-build's `ttyUSB0` printing `[poll]`), therefore no PHY config,
therefore no link. Ruled out by byte-identical comparison to the working
ancestor + the build log:

- Firmware is valid and identical to the working ancestor.
- Mi-V config (`MIV_RV32_C0.tcl`), APB bus (`CoreAPB3_0.tcl`), and the
  firmware→TCM association (`RAM.cfg`, applied via `configure_ram` in
  `5_program_design.tcl`) are identical to the ancestor.
- Build log confirms design-init ran: "Stage 1 / Stage 2_3 initialization client
  added to sNVM", "Memory files have been generated successfully", timing met on
  all corners.
- `top.tcl` connectivity (CORETSE_0 MDIO→PHY, `tse1_loopback`, `CORETSE_1:APBS`,
  both `LINK_OK` pins, Mi-V/UART clock+reset) is all present — same as ancestor.

**HYPOTHESIS (not isolated statically):** Since everything else matches the
working ancestor, the failure is introduced by the **`fabric_bridge` integration**
(`top.tcl` insertion + the eth RTL). On paper a data-path module shouldn't silence
the Mi-V, so the most likely mechanism is a **synth-vs-sim issue in the eth RTL**
(the `eth_hdr_*` struct-casts / packed structs) producing ill-defined silicon
logic with global side effects — exactly the class of bug this project has hit
before, and exactly what prod-build's `498e025`/`e33345a` fixes address. This was
**not confirmed** — it needs hardware iteration (see §6).

---

## 5. Recommendations for Peter

1. **Fastest path to a working clean forwarding + TYPE-drop branch:** bring
   `eth_sanitize`'s `eth_deframe`/`eth_reframe` up to the silicon-validated
   versions from prod-build — i.e. the in-band `tlen` sideband sampled at the
   `tvalid` handshake (`498e025`) and the locally-registered `tuser` (`e33345a`).
   `eth_sanitize`'s current deframe uses the pre-silicon header-capture that was
   already shown to mis-extract LENGTH on hardware.
2. Peter's TYPE-drop logic itself (`len_valid` / `beats_total=0`) is clean and
   functionally equivalent to prod-build's `reject L/T>1500`, which **passes on
   silicon** — so the drop approach is sound once the build runs.
3. The forwarding + TYPE-drop **feature is validated on silicon** (prod-build).
   The blocker is purely the `eth_sanitize` build not running the Mi-V.

## 6. Suggested isolation steps (to confirm the Mi-V-boot root cause)

- Rebuild `eth_sanitize` with the `fabric_bridge` **bypassed** (revert to the
  ancestor's direct MAC→MAC wiring). If the Mi-V then boots (UART + links), it
  proves the bridge integration is the cause.
- Or add a UART print as the *very first* instruction in `main()` (before
  `UART_init`'s dependencies) to see how far boot gets — silent means it never
  fetches valid instructions (TCM not initialized as expected / reset issue).
- Prime suspect: the `eth_hdr_*` struct-cast / packed-struct constructs in the
  eth RTL (synth-vs-sim). Compare against prod-build's reworked deframe.

---

## 7. State left on the hardware

- The board is left running the **known-good prod-build design** (forwarding +
  TYPE-drop **working**), so it's in a usable state.
- Both bitstreams are preserved on the Spark:
  - `~/fpe/top.job` — known-good prod-build (sha `1231a06f…`, currently flashed)
  - `~/fpe/eth_sanitize.job` — Peter's `eth_sanitize` build (sha `c0530c05…`)
- To re-flash either: copy it to `~/fpe/top.job` and run the flash per
  `~/fpe/HARNESS.md`.

## 8. How to reproduce the tests

On the Spark (host NICs, no VM/USB needed once a design is flashed):

- `bash ~/fpe/canon_fwd.sh 3000 64` — bidirectional forwarding (PASS = sanitized
  forced-DST frames both directions).
- `bash ~/fpe/type_drop.sh` — 3-phase TYPE-drop (PASS = LENGTH forwards, TYPE
  `0x86DD` dropped == 0, LENGTH still forwards). Added this session; also committed
  here under `tb/` for reference.

UART (Mi-V console): release the FlashPro FTDI from qemu, bind host `ftdi_sio`
(`new_id 1514 2008`), read `/dev/ttyUSB0 @115200` — see `~/fpe/HARNESS.md`.
