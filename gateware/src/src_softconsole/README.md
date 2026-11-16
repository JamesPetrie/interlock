# Mi-V firmware

The Mi-V soft RISC-V CPU on the FPGA runs this firmware to bring up both
CoreTSE MACs and whatever sits behind them on the board. The image is baked
into sNVM at FPGA configuration time and copied to TCM at boot.

For the MPF300-EVAL-KIT (the target) the firmware is `main.c` (iteration 2,
notes below) and the image Libero uses is `iog_cdr.hex` — the silicon-validated
SoftConsole build. It brings up both MACs, both VSC8575 copper ports and the
ZL30364 clock generator.

## Building (no SoftConsole needed)

```
apt-get install gcc-riscv64-unknown-elf picolibc-riscv64-unknown-elf   # once
make mpf300                  # -> build/iog_cdr_mpf300.hex (compare only; see below)
make install-mpf300          # replaces the committed iog_cdr.hex — do this on purpose only
```

The `Makefile` mirrors the AN4623 SoftConsole project's flags
(`-march=rv32im -mabi=ilp32 -O0`, `miv-rv32-ram.ld`) and fetches the HAL,
drivers and linker script from Microchip's AN4623 bundle into `build/an4623/`
(sha256-pinned; set `AN4623_DIR` to reuse an existing extraction).
`fw_stubs.c` stands in for the newlib-only `miv_rv32_syscall.c` under picolibc.
Output hex files are rebased to TCM offset 0 (no type-04 records), the format
`src_cfg/RAM.cfg` expects. The committed `iog_cdr.hex` is the SoftConsole build
of `main.c`; the Makefile deliberately does not overwrite it.

---

# `main.c` — MPF300-EVAL-KIT firmware (iteration 2 notes)

A single Mi-V core handles both PHYs — there's only one CPU instance in
the SmartDesign. The firmware does all the register writes sequentially
for both ports.

## Iteration 2 changes from iteration 1

- **Port-1 bring-up added.** New functions `tse1_init`, `phy1_init`,
  `Phy1_advertise`, `phy1_autonegotiation` — verbatim copies of the
  port-0 functions with three constants swapped:
  - PHY 28 (copper) → 29 (encoded `0x1C..` → `0x1D..`)
  - PHY 18 (SGMII)  → 19 (encoded `0x12..` → `0x13..`)
  - TSE_BASEADDR (0x60000000) → TSE1_BASEADDR (0x60003000) for MAC CSR writes
  - **All MDIO transactions** still go through CoreTSE_0's MDIO master
    (TSE_BASEADDR for the MII command/status registers); only the encoded
    PHY-address target changes. CoreTSE_1's MDIO pins aren't wired —
    see `top.tcl`.
- **Bounded polling loops in port-1 code only.** AN4623's port-0 polling
  loops are unbounded `while(1)`s; if the PHY is absent they hang. The
  port-1 mirrors use a bounded loop counter so a missing/unresponsive
  PHY 29 logs a warning instead of locking up the CPU. Port-0 code is
  left untouched to avoid introducing any regression.
- **Status dump for both ports** at end of init: reads PHY status
  register (reg 1) from copper + SGMII sides of both ports and prints
  link / autoneg-complete bits.
- **Idle poll loop covers both ports** — prints status of all four PHY
  blocks once per ~1s.
- **Pre-init MDIO scan** retained from iter-1 — it runs after port-0
  init (CoreTSE_0's MDIO clock is configured then) but before port-1
  init starts. Output identifies the actual PHY addresses on the bus.

## Port-1 PHY address guesses

Iteration 2 assumes:

| Port | Copper PHY | SGMII PHY |
|---|---|---|
| 0 (verified, AN4623) | 28 (`0x1C`) | 18 (`0x12`) |
| 1 (guessed) | 29 (`0x1D`) | 19 (`0x13`) |

The `[scan]` output at boot will print every responding PHY's ID
register. If port 1's addresses are not 29/19, edit the `PHY1_*` defines
near the top of `main.c` and re-flash.

## Expected boot output

```
[boot] interlock firmware iter-2 starting
[boot] configure_zl30364 (clock generator)
[boot] configure_zl30364 done
[boot] tse_init (MAC 0 @ 0x60000000, PHY 28 copper / 18 SGMII)
[boot] tse_init done
[scan] sweeping MDIO 0..31 via TSE @ 0x60000000
[scan]   addr=18  ID1=0x0007  ID2=0x0429
[scan]   addr=19  ID1=0x0007  ID2=0x0429    ← if port 1 SGMII visible
[scan]   addr=28  ID1=0x0007  ID2=0x0429
[scan]   addr=29  ID1=0x0007  ID2=0x0429    ← if port 1 copper visible
[scan] done
[boot] tse1_init (MAC 1 @ 0x60003000, PHY 29 copper / 19 SGMII)
[boot] tse1_init done
[boot] Phy_advertise (port 0, PHY 28)
[boot] Phy_advertise done
[boot] Phy1_advertise (port 1, PHY 29)
[boot] Phy1_advertise done
[boot] phy_autonegotiation (port 0)
[boot] phy_autonegotiation done
[boot] phy1_autonegotiation (port 1)
[boot] phy1_autonegotiation done
[state] port0 copper PHY 28 status=0x.... link=1 aneg_done=1
[state] port0 SGMII  PHY 18 status=0x.... link=1 aneg_done=1
[state] port1 copper PHY 29 status=0x.... link=? aneg_done=?
[state] port1 SGMII  PHY 19 status=0x.... link=? aneg_done=?
[probe] CoreTSE_1 MAC_CONFIG_1 reads 0x00000005
[boot] init complete — polling both ports
[poll t=0] p0c=0x.... p0s=0x.... p1c=0x.... p1s=0x.... (link bits: 1100)
[poll t=1] p0c=0x.... p0s=0x.... p1c=0x.... p1s=0x.... (link bits: 1111)
...
```

The link bits in the poll loop are `p0c p0s p1c p1s` — all-1s
means full bidirectional bridge is up.

## Diagnostic decoding

| Boot output pattern | Implies |
|---|---|
| `[scan]` shows no addr=29 / addr=19 | Port-1 PHY addresses are NOT 29/19. Try other +N offsets or read the VSC8575 strap pins from schematics. |
| `[phy1] WARNING: PHY 29 ID register reads as 0xFFFF` | Same — port 1 copper PHY not at address 29. Update `PHY1_COPPER_ADDR`. |
| `[scan]` shows addr=29 + addr=19 but `[state] port1 ... link=0 aneg_done=0` | Init code is touching the right PHY but autoneg isn't completing — likely SGMII-side config issue, possibly VSC8575 page semantics needing a different sequence. |
| All four `[state] ... link=1 aneg_done=1` | Both ports up. Bridge should pass traffic on physical RJ45s. |
| `[probe] CoreTSE_1 MAC_CONFIG_1 reads 0xFFFFFFFF` | TSE1_BASEADDR is wrong — APB decode not hitting CoreTSE_1. Verify CoreAPB3 slot mapping in `top.tcl`. |

## Workflow

See `../../../docs/firmware-iteration.md` for SoftConsole setup.
Short version:

1. Build `main.c` in SoftConsole's `Debug` configuration → produces
   `Debug/iog_cdr.hex`.
2. Get the new hex onto Hetzner: `scp ~/softconsole-workspace/iog_cdr/Debug/iog_cdr.hex <hetzner>:/tmp/iter2.hex`.
3. On Hetzner, replace `gateware/src/src_softconsole/iog_cdr.hex` with the
   new hex.
4. Rebuild the Libero bitstream (~25 min).
5. Flash the new `.job` via FlashPro Express (~10 min).
6. Open `screen /dev/ttyUSB0 115200` to watch the boot output.

JTAG-load fast iteration (~10 sec/iter) is documented but not currently
working with this OpenOCD/Mi-V combo — see `docs/firmware-iteration.md`.

## Files NOT in this directory

The rest of the AN4623 SoftConsole project (drivers, HAL, linker scripts)
is Microchip's distribution; `make fetch` downloads and unpacks it into
`build/an4623/` (gitignored), or point `AN4623_DIR` at an existing copy such
as `/root/fpga/an4623/`. Only the `.c` sources in this directory
are ours.
