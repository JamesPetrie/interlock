# interlock — agent instructions

PolarFire interlock prototype. Starts from Microchip's AN4623 ethernet
demo and evolves toward the verifier interlock from the *Inference
Verification Prototype* design doc.

For broader Hetzner-box + Libero context (wrappers, license daemon, vault
layout, the "Cannot find Spirit core configuration file" playbook, board
specifics), see `/root/fpga/CLAUDE.md` — that file is auto-loaded when CWD
is anywhere under `/root/fpga/`, including this repo, so don't duplicate
its content here.

## What's pinned in `gateware/common.tcl`

The AN4623 bundle was written for Libero v2022.3. In v2024.2 the following
IP versions had to be bumped (catalog packages broke at the original
versions, surfaced as "Cannot find Spirit core configuration file"):

- `PF_INIT_MONITOR` 2.0.304 → **2.0.307**
- `PF_IOD_CDR` 2.4.105 → **2.4.110**

Both `common.tcl` AND `src/src_components/<core>.tcl` reference the
version — edit both when bumping.

## Build / iterate loop

```
./build.sh         # ~1h; logs to /tmp/build_*.log
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
3. Wire it into the SmartDesign in the same Tcl (`sd_instantiate_component`,
   `sd_connect_pins`, etc.).
4. Add pin constraints in `gateware/src/src_constraints/`.
5. `./build.sh`.

For firmware changes (the precompiled `iog_cdr.hex` lives at
`gateware/src/src_softconsole/iog_cdr.hex`): rebuild the .hex with
SoftConsole or bare `riscv32-unknown-elf-gcc`, replace the file, and run
`./build.sh`. The .hex gets baked into the Mi-V's TCM LSRAM by P&R, so
firmware-only changes still require a full Libero rebuild unless you use
Libero's "update memory content" tool.

## Git discipline

- Direct-to-main commits are fine for small iterations. PRs for anything
  involving a structural change to the SmartDesign or a new vendor IP.
- Commit the patched Tcl, not the build output. `gateware/Libero_Project/`
  is gitignored.
- The .hex firmware is in-tree as a binary for now (~32 KB). When the
  RISC-V toolchain is set up, we can move to source + build-time
  compilation.
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
