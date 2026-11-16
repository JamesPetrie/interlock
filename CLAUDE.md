# interlock — agent instructions

PolarFire interlock prototype. Starts from Microchip's AN4623 ethernet
demo and evolves toward the verifier interlock from the *Inference
Verification Prototype* design doc.

For broader Hetzner-box + Libero context (wrappers, license daemon, vault
layout, the "Cannot find Spirit core configuration file" playbook, board
specifics), see `/root/fpga/CLAUDE.md` — that file is auto-loaded when CWD
is anywhere under `/root/fpga/`, including this repo, so don't duplicate
its content here.

## Target board

The target is the **MPF300-EVAL-KIT** (`mpf300`, MPF300TS-1FCG1152I,
validated on silicon with Libero v2024.2). `./build.sh` builds it, and
README / `docs/SETUP.md` / NOTES.md / `docs/flashing-and-testing.md`
describe it.

`gateware/common.tcl` selects the board from `INTERLOCK_BOARD`
(`./build.sh --board <board>`, default `mpf300`) and holds the per-board
device settings and IP-version table. Board-specific Libero files live in
`gateware/src/boards/<board>/`: `components.tcl` (PHY-side IP + init
monitor), `top.tcl` (the top SmartDesign), `io_constraints.pdc`,
`timing_user_constraints.sdc`, and `prog_io_states.ios` where needed.
Everything else (`src_components/`, `src_hdl/`, flow scripts) is shared.

Rules: never hardcode a die/package or IP version outside `common.tcl`;
component Tcl files use `$<CORE>ver`; anything that differs between boards
goes under `boards/<board>/`, not behind `if` blocks in shared files. Any
board other than the kit is a temporary stand-in, not a second target: it
is documented **only** in its own `docs/<board>-port.md` (design, pinout,
toolchain, bring-up, firmware, status), and the kit docs listed above —
this file included — stay free of it. `ls gateware/src/boards/ docs/` shows
which stand-ins exist.

### Build box

Libero SoC v2025.2 is the only Libero on this box since 2026-11-16
(`/usr/local/microchip/Libero_SoC_2025.2/Libero_SoC/Designer/bin/libero`;
on PATH in login shells via `~/.bashrc`, otherwise pass `LIBERO_BIN=<path>`
to `build.sh`). The kit flow runs on it unchanged apart from the automatic
`PF_INIT_MONITOR` switch described below (verified 2026-11-16 through
create+constrain).

## What's pinned in `gateware/common.tcl`

The AN4623 bundle was written for Libero v2022.3. In v2024.2 the following
IP versions had to be bumped (catalog packages broke at the original
versions, surfaced as "Cannot find Spirit core configuration file"):

- `PF_INIT_MONITOR` 2.0.304 → **2.0.307** (v2024.2). On Libero ≥ 2025.1
  `common.tcl` switches to **2.0.308** by itself (`get_libero_release`): the
  2.0.307 package extracts only its `core_xml` fileset there and hits the
  same Spirit error (NOTES.md).
- `PF_IOD_CDR` 2.4.105 → **2.4.110**

Component Tcl files take their version from `common.tcl` (`$CORETSEver`,
`$PF_CCCver`, ...), so a bump is a one-line change in the right board's
block of the version table.

## Build / iterate loop

```
./build.sh         # MPF300 kit, ~1h; logs to /tmp/build_*.log (LIBERO_BIN=<path> if libero is not on PATH)
```

The `.job` lands in `gateware/Libero_Project/designer/top/export/top.job`.
Build output dir is `.gitignored`.

If `build.sh` fails fast with a Spirit-config error, the most likely cause
is a stale or partial entry in the IP vault. Clean and retry:

```
# wipe the offending core's vault dir + remove its entry from vault/index.xml
# (commands in /root/fpga/CLAUDE.md)
```

## Modifying the design

For a self-contained RTL addition (e.g., a packet counter on LEDs):

1. Drop the SystemVerilog file into `gateware/src/src_hdl/`.
2. Add `import_files -hdl_source {./src/src_hdl/<file>.sv}` in
   `gateware/src/1_create_design.tcl`.
3. Wire it into the SmartDesign in `gateware/src/boards/mpf300_eval/top.tcl`
   (`sd_instantiate_hdl_module`, `sd_connect_pins`, etc.); mirror it in any
   other `boards/<board>/top.tcl` that should carry it.
4. Add pin constraints in `gateware/src/boards/mpf300_eval/io_constraints.pdc`.
5. `./build.sh`.

For firmware changes: `main.c` and a Makefile are in
`gateware/src/src_softconsole/`; `make -C gateware/src/src_softconsole mpf300`
rebuilds `build/iog_cdr_mpf300.hex` with the Ubuntu `gcc-riscv64-unknown-elf`
+ `picolibc-riscv64-unknown-elf` toolchain (the AN4623 HAL/drivers are
fetched into `build/` on first use, sha256-checked). The committed
`iog_cdr.hex` is still the silicon-validated SoftConsole build — replace it
only on purpose (`make install-mpf300`). The .hex gets baked
into the Mi-V's TCM LSRAM by P&R, so firmware-only changes still require a
full Libero rebuild unless you use Libero's "update memory content" tool.

## Git discipline

- Direct-to-main commits are fine for small iterations. PRs for anything
  involving a structural change to the SmartDesign or a new vendor IP.
- Commit the patched Tcl, not the build output. `gateware/Libero_Project/`
  is gitignored.
- The .hex firmware images are in-tree as binaries (~20-40 KB each) next to
  their sources; the Makefile rebuilds them, but Libero consumes the
  committed files.
- The AN4623 source bundle is NOT vendored as a zip — only the patched
  TCL_Scripts subset that we actually use. The full bundle (including
  pre-built `top.job` and SoftConsole Eclipse project) is at
  `https://ww1.microchip.com/downloads/aemDocuments/documents/FPGA/SOCDesignFiles/mpf_an4623_v2022p3_df.zip`;
  fetch with the User-Agent gotcha noted in `/root/fpga/CLAUDE.md`.

## Things NOT to do

- **Don't edit `gateware/Libero_Project/`** — it's auto-generated and
  gitignored. Changes go in `gateware/src/` instead.
- **Don't bypass `download_core`** in `script.tcl`. Pre-downloading the
  pinned IPs into the vault before `create_and_configure_core` is the
  pattern that works in v2024.2 (per Microchip's own SmartHLS scripts).
- **Don't add `-download_core` flag** to `create_and_configure_core` for
  SystemBuilder cores in this design. Mixing the standalone `download_core`
  pre-pull with the inline `-download_core` flag was found to leave
  SystemBuilder cores in a broken partial state.
- **Don't commit the AN4623 zip** (it's 13 MB) or the `Programming_Job/`
  prebuilt `top.job`. Both are recoverable: zip from the Microchip CDN
  (see NOTES.md), prebuilt `.job` is in the zip.
