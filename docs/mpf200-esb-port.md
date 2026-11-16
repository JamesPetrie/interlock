# Porting the interlock to the MPF200-ETH-SENSOR-BRIDGE-R2

*Started 2026-11-16, branch `feature/mpf200-esb` (on top of `refactor/board-dirs`, which moved the kit's board files under `gateware/src/boards/` without any MPF200 content). Status: first MPF200T bitstream built with Libero SoC 2025.2 on 2026-11-16 (§7); nothing has run on the board yet (§8).*

**Scope.** The MPF300-EVAL-KIT remains the official target of this repository; the MPF200-ETH-SENSOR-BRIDGE-R2 is a temporary stand-in while the kit is out of stock. This document is the single place for everything MPF200-specific — README, `docs/SETUP.md`, NOTES.md and `docs/flashing-and-testing.md` describe the kit and only point here. §9 says what to do when the kit is back.

The MPF300-EVAL-KIT the interlock was developed on is out of stock, so the prototype runs in the meantime on Microchip's **PolarFire Ethernet Sensor Bridge, Rev 2** (`MPF200-ETH-SENSOR-BRIDGE-R2`, the board NVIDIA's Holoscan Sensor Bridge uses). The interlock core does not care which board it runs on; what changes is everything between the CoreTSE MACs and the connectors. This document records the board facts the port relies on (with sources), the design deltas, the toolchain situation, and the bring-up plan.

## 1. What stays, what goes

| MPF300-EVAL-KIT (AN4623 baseline) | MPF200-ETH-SENSOR-BRIDGE-R2 | Notes |
|---|---|---|
| `recomp_ilock_core` and everything below it (deframe, canon, commit, cert, SHA/HMAC, buffers) | **unchanged** | plain SystemVerilog on the 80 MHz fabric clock |
| CoreTSE ×2 in TBI mode (1G MAC + full PCS, MDIO master/slave) | **unchanged**, same component Tcl, same APB map | the PCS still does 8b/10b, comma alignment and auto-negotiation |
| Mi-V + CoreAPB3 + CoreUARTapb + COREJTAGDEBUG + CORERESET_PF + PF_CCC (50 → 80 MHz) | **unchanged** | the board also has a 50 MHz oscillator, so even the CCC config is identical |
| PF_IOD_CDR ×2 (SGMII over LVDS GPIO) + PF_IOD_CDR_CCC (125 MHz TX clock) + INBUF_DIFF for the 125 MHz refclk | **PF_XCVR_ERM** (2 lanes, PMA mode, 10-bit) + **PF_TX_PLL** + **PF_XCVR_REF_CLK** (156.25 MHz) | AN4623 Appendix 4 recipe, see §3.2 |
| VSC8575 quad copper PHY behind two RJ45s, external MDIO bus, `PHY_RST`, `coma_mode`, `REF_CLK_SEL` | **two SFP+ cages**, no PHY, no external MDIO | an SFP module is the "PHY"; both CoreTSE MDIO buses are looped back internally |
| CoreSPI → ZL30364 clock generator (firmware `configure_zl30364`) | CoreSPI kept, pins tied off; firmware call dropped | keeps the APB slot map so either firmware image fits the design |
| `SSDetect` (IOD stream-start detect) | gone | the transceiver needs no stream-start hint |
| `RESET_N` push button | none on Rev 2 (`EXT_RST_N` tied high) | reset comes from `DEVICE_INIT_DONE` + PLL locks only |
| 8 user LEDs | 4 user LEDs | mapping in §3.4 |
| Mi-V firmware `main.c` (`iog_cdr.hex`) | `main_mpf200.c` (`iog_cdr_mpf200.hex`) | same MAC init; PCS AN setup replaces all VSC8575 code |
| Libero 2024.2 | **Libero ≥ 2025.2** | the MPF200T die is Silver-licensed only from 2025.2 (§4) |

Repository shape: board-specific Libero files now live in `gateware/src/boards/<board>/` (`components.tcl`, `top.tcl`, `io_constraints.pdc`, `timing_user_constraints.sdc`, PHY-side IP configs, and `prog_io_states.ios` where a board needs one); `gateware/common.tcl` selects the board from `INTERLOCK_BOARD` and holds a per-board device + IP-version table; `./build.sh --board mpf200|mpf300` sets it. Shared component Tcl files now take their IP version from `common.tcl` (`$CORETSEver` etc.) instead of hardcoding it.

## 2. Board facts the port relies on

All items were taken from Microchip's Rev 2 schematic (`PolarFire_Ethernet_Sensor_Bridge_REV2_Schematics.pdf`, 7 Nov 2025), the user guide DS50003757B (Jan 2026), and Microchip's own Libero 2025.2 reference design for the board ([microchip-fpga-solutions/mpf200-esb-mipi](https://github.com/microchip-fpga-solutions/mpf200-esb-mipi)); every ball number was then cross-checked against Libero's FCG784 package table (`Libero/data/aPA5M/PA5M200/fcg784.ddf`).

| Item | Value | Source |
|---|---|---|
| FPGA | MPF200T, FCG784, extended temp (`MPF200T-1FCG784E` in the reference design's component headers; its `new_project` uses speed `STD`, VDD 1.0 V) | reference design, user guide §1.3 |
| Core voltage | 1.0 V or 1.05 V, selected by jumper **J18** (2-3 = 1.0 V, 2-1 = 1.05 V) | user guide table 1-3 |
| SFP1 | XCVR quad 2 **lane 0**: RX P/N **AC23/AC24**, TX P/N **AC27/AC28** | schematic sheet 6, reference PDC |
| SFP2 | XCVR quad 2 **lane 1**: RX P/N **AD25/AD26**, TX P/N **AE27/AE28** | schematic sheet 6, reference PDC |
| Transceiver refclk | 156.25 MHz LVDS oscillator X1 → `XCVR_2A_REFCLK_P/N` **W23/W24** (the only refclk on quad 2) | schematic sheet 6, user guide table 1-2, reference `PF_XCVR_REF_CLK` |
| Fabric clock | 50 MHz oscillator X2 (DSC1001, 3.3 V) → **B7** (`GPIO177PB5/CLKIN_W_0`) | user guide §3.10, schematic sheet 11 |
| SFP sideband (Bank 5, 3.3 V, 10 kΩ pull-ups) | SFP1: TX_DISABLE **F4**, LOS **F3**, MOD_DEF0 E3, TX_FAULT E2, RS G5, SCL D3, SDA D1. SFP2: TX_DISABLE **G4**, LOS **K8**, MOD_DEF0 F2, TX_FAULT L8, RS E1, SCL C2, SDA C1 | schematic sheets 6 + 11 |
| UART | FT4232H channel C: `FTDI_UART_C_TXD` → FPGA **E6** (our RX), `FTDI_UART_C_RXD` ← FPGA **D6** (our TX). Channel D is on F5/E5. Channel A is the on-board FlashPro5 JTAG | schematic sheets 11 + 13, user guide §3.4 |
| LEDs (HSIO Bank 1, 1.2 V, active high) | LED1 **AD18**, LED2 **AE18**, LED3 **AB19**, LED4 **AC18** | user guide table 3-1, schematic sheet 4 |
| Buttons / DIP switches | **none** wired to fabric I/O on Rev 2 (the user-guide component table still lists SW1/SW2/SW8 from Rev 1; the Rev 2 schematic has no such parts). `DEVRST_N` (J15) is driven by an MCP121 supervisor only | schematic sheets 12, 18-20 |
| JTAG | TCK J9, TDI J14, TDO H11, TMS J12, TRSTB K14 (dedicated); programmed through the on-board eFP5 over USB-C | schematic sheet 12, user guide §6 |
| Power | 12 V adapter on J25; USB-C for the FTDI/eFP5 | user guide §1.6 |
| Other quads | XCVR1/XCVR3 go to the FMC (J1) with 148.5/125 MHz oscillators; irrelevant here | user guide §1.2 |

The schematic text layer puts each ball number one row above its pin row; that convention was confirmed against three independent anchors (B7 from the user guide, the four LED balls, and the reference design's I²C/MIPI pins) before reading off the sideband and UART balls.

## 3. Design

```
                     MPF200T                                              
 SFP1 ──RX/TX──┐  ┌──────────────┐ 10-bit TBI  ┌──────────┐  MAC FIFO   ┌────────────────────┐
 (cage B,      ├──┤ PF_XCVR_ERM  ├─────────────┤ CORETSE_0├─────────────┤                    │
  lane 0)      │  │   lane 0     │ RX/TX_CLK_R │ (TBI,    │   80 MHz    │  recomp_ilock_core │
               │  │              │   125 MHz   │  PCS 18) │             │     (unchanged)    │
 SFP2 ──RX/TX──┤  │   lane 1     ├─────────────┤ CORETSE_1├─────────────┤                    │
 (cage A,      │  └──────┬───────┘             │ (PCS 19) │             └────────────────────┘
  lane 1)      │  CLKS_FROM_TXPLL               └────┬─────┘  APB            Mi-V + UART + JTAG
               │  ┌──────┴───────┐  ┌──────────────┐ │   (MDIO looped back per MAC: tse1_loopback ×2)
 156.25 MHz ───┼──┤ PF_TX_PLL    ├──┤PF_XCVR_REF_CLK│ │
 (W23/W24)     │  │ 5000 Mb/s ÷4 │  └──────────────┘ │
               │  └──────────────┘                    │
 50 MHz (B7) ──┼── PF_CCC ── 80 MHz ──────────────────┴── CORERESET_PF ── AND(TX PLL lock) ── MAC/core resets
 LOS ──INV──── SIGNAL_DETECT       TX_DISABLE ◄── INV(fabric_reset_n)      LED1..4 ◄── link0, link1, pkt0, rcg_err
```

### 3.1 Why 1G over the transceiver, not 10G

The board's two network ports are 10G SFP+ cages on transceiver lanes; there is no RJ45 and no copper PHY. Two options existed:

- **Keep 1G**: run the transceiver at 1.25 Gb/s and keep CoreTSE's complete, silicon-validated PCS/MAC and firmware path. Microchip documents exactly this (AN4623 Appendix 4, "1G Ethernet BASE-T and BASE-X Using Transceiver"). Needs 1G SFP modules — copper 1000BASE-T SFPs to reach the existing bench (NICs, Mac, Raspberry Pi), or 1000BASE-SX/LX optics.
- **Go 10G**: Core10GMAC + 10GBASE-R PCS at 64 bit / 156.25 MHz, plus the width/clock shims already written for the VPK180 port. Core10GMAC is a separately licensed core (Microchip's reference design README says so explicitly), the bench has no 10G peers, and nothing in the interlock needs the bandwidth.

1G it is. Peers stay on copper; the SFP+ cage with a 1000BASE-T SFP module is electrically a 1G port. The Wiitek SFP-10G-T-S module shipped with the kit is a 10GBASE-T part and is **not** expected to work with this design.

### 3.2 Transceiver configuration (AN4623 Appendix 4, table 7-2, adapted)

| Block | Setting | Why |
|---|---|---|
| `PF_XCVR_REF_CLK_C0` | REF_CLK_0 differential, no fabric copy | the quad-2 pad pair W23/W24 |
| `PF_TX_PLL_C0` | REF 156.25 MHz, OUT (bit clock) 2500 MHz = base data rate 5000 Mb/s | the appendix uses 125 MHz → 5000 Mb/s; 5000/156.25 = 32 is an integer, so only the reference changes |
| `PF_XCVR_ERM_C0` | 2 lanes, duplex, 1250 Mb/s, TX clock division 4, **PMA mode**, fabric width 10 bits (125 MHz), CDR "lock to data", CDR ref 156.25 MHz dedicated, regional TX/RX clocks, PCS reset RX-only, ERM off | PMA mode because CoreTSE's TBI block already is the PCS; both lanes share the PLL and reference; ERM off removes the CTRL_CLK/CTRL_ARST_N requirement |

Connections mirror the IOD CDR ones one-for-one: `LANEn_RX_DATA[9:0]` → `RCG`, `TCG` → `LANEn_TX_DATA[9:0]`, `LANEn_RX_CLK_R` → `RXCLK` + `TBI_RX_CLK`, `LANEn_TX_CLK_R` → `TXCLK` + `TBI_TX_CLK` (each MAC now has its own 125 MHz TX clock instead of the shared `TX_CLK_G`). The TX PLL's `CLKS_TO_XCVR` bus feeds `CLKS_FROM_TXPLL_0`; the refclk feeds the PLL and both `LANEn_CDR_REF_CLK_0` inputs.

### 3.3 Resets, clocks, sideband

- `CORERESET_PF` keeps its inputs (`FABRIC_POR_N`, `DEVICE_INIT_DONE`, the PF_CCC lock, VDDI status) but the VDDI status now comes from **Bank 5** (`pf_init_monitor_0` variant) — the bank that powers the oscillator, UART and sideband. Gating on Bank 6 as the kit did would hold the design in reset if that bank is unpowered on this board.
- `EXT_RST_N` is tied to VCC (no button). A full reset is a power cycle or re-programming.
- The CoreTSE/core reset is `FABRIC_RESET_N AND PF_TX_PLL PLL_LOCK` (the kit used the IOD CDR CCC's lock; the TX PLL now owns the 125 MHz TBI TX clocks). The XCVR lane PMA/PCS resets are driven by `FABRIC_RESET_N`.
- `SFPx_TX_DISABLE = NOT FABRIC_RESET_N`: the board's 10 kΩ pull-ups keep modules dark until the fabric runs, including during programming (so the kit's `prog_io_states.ios` has no counterpart here).
- `CoreTSE SIGNAL_DETECT = NOT SFPx_LOS` instead of a constant 1: no module or no light means "no signal", which is also what a copper SFP reports when its cable is unplugged.
- `MOD_DEF0`, `TX_FAULT`, `RS`, and the module I²C are left unconnected (pins documented in the PDC for later).

### 3.4 LEDs

| LED | Ball | Meaning |
|---|---|---|
| LED1 | AD18 | `LINK_OK` — port 0 (SFP1) PCS link up (`ANX_STATE[8]`) |
| LED2 | AE18 | `LINK_OK_1` — port 1 (SFP2) PCS link up |
| LED3 | AB19 | `PKT_LED` — port 0 receive heartbeat (`pkt_counter`) |
| LED4 | AC18 | `LED_RCG_ERR` — sticky 8b/10b decode error on either port (clears only on reset); lit during bring-up means the lane/refclk/polarity needs attention |

The kit's four extra debug LEDs (`MTXACPT`, port-1 RX count) have no home here; the UART poll output covers link state.

### 3.5 Firmware (`gateware/src/src_softconsole/main_mpf200.c`)

Same MAC CSR sequence as AN4623's `tse_init` for both MACs; no ZL30364, no VSC8575. Each MAC's internal PCS (MDIO address 18 on `TSE_BASEADDR`, 19 on `TSE1_BASEADDR` — each on its own looped-back bus, as `tse1_loopback` already did for CORETSE_1 on the kit) is programmed per the CoreTSE v3.2 handbook §3.5: the AN Advertisement register (0x04) selects the mode — `0x0001` for **SGMII** (default; copper 1000BASE-T SFPs present an SGMII PHY to the host), `0x0020` for **1000BASE-X** full duplex (optical modules; build with `MODE=basex`) — then AN enable + restart in register 0. Every MDIO poll is bounded; a port whose AN has not completed gets a restart every second so hot-plugged modules link up. The UART prints the two PCS status words once a second at 115200 8N1 on FTDI channel C.

The image is built with the Ubuntu `gcc-riscv64-unknown-elf` + picolibc toolchain (`make -C gateware/src/src_softconsole mpf200 install`); HAL/drivers/linker script are pulled from Microchip's AN4623 bundle at build time (sha256-pinned), not vendored. The same Makefile rebuilds the kit's `main.c`; sanity check: the unmodified AN4623 `main.c` builds to within 2% of Microchip's shipped ELF size with these flags. `fw_stubs.c` replaces the newlib-only `miv_rv32_syscall.c`.

Expected boot output (UART, 115200 8N1):

```
[boot] interlock firmware mpf200-esb (SFP via XCVR, PCS SGMII) starting
[boot] tse_mac_init MAC0 @0x60000000, MAC1 @0x60003000
[pcs] port0/SFP1 addr=18 status=0x01.. mode=SGMII adv=0x0001 tbi_ctrl=0x....
[pcs] port1/SFP2 addr=19 status=0x01.. mode=SGMII adv=0x0001 tbi_ctrl=0x....
[an]  port0/SFP1 status=0x.... link=1 an_done=1 lpa=0x....
[an]  port1/SFP2 status=0x.... link=1 an_done=1 lpa=0x....
[boot] init complete — polling both ports
[poll t=0] p0s=0x.... p1s=0x.... (link bits: 11 an_done: 11)
```

`status=0xFFFF` on a `[pcs]` line means the MAC's internal MDIO slave did not answer (loopback or MAC reset problem in the gateware); the idle PCS status with no module is `0x0149`. A port with `an_done=0` gets an AN restart every second, so modules plugged in after boot still link up.

**Host-side test** (`make -C gateware/src/src_softconsole test`, host gcc only): compiles `main_mpf200.c` with `-DHOST_TEST` against a mock of the two CoreTSE MDIO-management blocks and their TBI/PCS slaves (`test/mock_tse.c`, register behaviour per the CoreTSE v3.2 handbook §3.1/§3.5) and checks, for both the SGMII and the 1000BASE-X build: the MAC init sequence equals the kit firmware's, PCS 18/19 are only ever addressed through their own MAC, the advertisement/enable/restart writes, bounded waits when a module is missing or MDIO never goes idle, the NO RESPONSE path, and the poll loop's AN re-kick on an unlinked port only. The hooks do not change the target image (the SGMII hex is byte-identical with and without them).

## 4. Toolchain and licensing

- **Libero 2024.2 + the Silver license refuses the MPF200T die** (`Unable to select die 'MPF200T'. The die value must be one of: MPF050T MPF050TL MPF100T MPF100TL`). The license file itself carries only the tier (`INCREMENT ACTEL_BASESOC` = Silver); the part-number-to-tier table ships inside each Libero release (`Libero/data/sprp/G5_pTab_sprp.csv`). In 2024.2 the kit part `MPF300TS-1FCG1152I` is a listed Silver ("Base") exception while every `MPF200T` part is Gold ("Vista"); Microchip's 2025.2 release notes: *"The Libero SoC 2025.2 release upgrades the Silver license for MPF200T device (FCG784 package), with extended device range enabled"* (2026.1 adds more MPF200T packages). So the MPF200 build needs **Libero ≥ 2025.2** and the **same** `License.dat`.
- **Done on the build box on 2026-11-16:** Libero 2024.2 was uninstalled (user-approved) and **Libero SoC 2025.2** installed headless from the online installer (`Libero_SoC_2025.2_online_lin.sh install --root /usr/local/microchip/Libero_SoC_2025.2 --accept-licenses --accept-messages --confirm-command`; 34 GB, needs 45 GB free during install; the installer only runs through its `.sh` wrapper — the bare `.bin` needs the bundled `libpng15` — and `--accept-messages` cannot be combined with `--default-answer`). New layout: `/usr/local/microchip/Libero_SoC_2025.2/Libero_SoC/Designer/bin/libero` (daemons in `.../Designer/bin64`); `License.dat`, `/etc/systemd/system/lmgrd.service` and `~/.bashrc` were repointed. Probe result with the existing Silver license: `new_project -die MPF200T -package FCG784 -speed STD -part_range EXT` **accepted**. Consequence for the kit flow: its validated `PF_INIT_MONITOR` 2.0.307 package extracts without its configurator fileset under 2025.x (a clean refetch gives the same), so `common.tcl` keeps 2.0.307 as the kit's pin and switches to 2.0.308 by itself when `get_libero_release` is 2025.1 or newer; `PF_CCC` 2.2.220 configures fine. Verified 2026-11-16: the `mpf300` flow runs through design creation and constraints on 2025.2 with 2.0.308 / 2.2.220 (NOTES.md).
- **Two Ubuntu-specific fixes 2025.2 needed before `download_core` worked** (symptom: every core fails with a bare `INFO:Could not download the core ...`; `restore_repositories` revealed the real error `error setting certificate verify locations: CAfile: /etc/pki/tls/certs/ca-bundle.crt`):
  1. Libero 2025.2 downloads over HTTPS with the Red Hat CA-bundle path hardcoded. Ubuntu keeps the bundle elsewhere, so: `mkdir -p /etc/pki/tls/certs && ln -s /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt`.
  2. The IP-repository list is per user and starts with only the local catalog; the three web repositories must be registered once (`restore_repositories`, or `add_repository -location www.microchip-ip.com/repositories/{SgCore,DirectCore,Firmware}`). `gateware/script.tcl` now calls `restore_repositories` (guarded) before its download loop.
  Also note the 2025.2 vault lives at `/usr/local/microchip/common/vault` (`install.def: VAULT_LOCATION`), not `~/.actel/vault` as in 2024.2 — `setup.sh`'s vault checks still point at the old path.
- IP versions for the mpf200 board come from Microchip's 2025.2 reference design: PF_CCC 2.2.222, PF_INIT_MONITOR 2.0.308, PF_TX_PLL 2.0.304, PF_XCVR_REF_CLK 1.0.103, PF_XCVR_ERM 3.1.206 (its package metadata says min Libero 2025.1, so under 2024.2 it fails with the same "Cannot find Spirit core configuration file" message as the stale-vault bug in NOTES.md — different cause, the wipe-and-refetch recipe does not help; 3.1.205 is the 2024.2 equivalent and accepts the same parameters). The shared cores keep their 2024.2 versions (CoreAPB3 4.2.100 and CORERESET_PF 2.3.100 are confirmed present in 2025.2; CoreTSE 3.2.119, MIV_RV32 3.0.100, CoreUARTapb 5.7.100, CORESPI 5.2.104, COREJTAGDEBUG 4.0.100 are expected to be — bump in `common.tcl` if `download_core` says otherwise).
- Device settings (`STD`, `EXT`, 1.0 V) copy the reference design's `new_project`. If the board turns out to carry a `-1` part with J18 on 1.05 V, switch `eval_part_speed`/`eval_die_voltage` in `common.tcl`; a STD-timed bitstream is valid on -1 silicon either way.
- `INTERLOCK_TCL_OVERRIDE=<file.tcl>` lets a build override anything from the board table after the fact (used for the dry run below); never needed for a normal build.

## 5. Build, flash, watch

```
# gateware (Libero >= 2025.2; the build box has it on PATH, otherwise:
#   LIBERO_BIN=/usr/local/microchip/Libero_SoC_2025.2/Libero_SoC/Designer/bin/libero)
./build.sh --board mpf200
# firmware image (already committed as iog_cdr_mpf200.hex; rebuild after editing main_mpf200.c)
make -C gateware/src/src_softconsole mpf200 install            # MODE=basex for optical SFPs
```

Flashing: the board's on-board FlashPro5 enumerates over the USB-C port; FlashPro Express loads `gateware/Libero_Project/designer/top/export/top.job` and runs it (user guide §6). The Mi-V console is FT4232H channel **C** — normally the third `ttyUSB` of the board (`/dev/ttyUSB2`, 115200 8N1); channel A is JTAG, so the same FTDI-sharing caveat as on the kit applies (`docs/flashing-and-testing.md` §UART).

## 6. Bring-up plan

1. **Program and watch the UART.** Expect `[boot] ... starting`, both `[pcs] ... status=0x01xx` lines (a `0xFFFF` means the MDIO loopback or MAC reset is wrong), then the per-second poll.
2. **Link up with copper SFPs**, one per cage, cabled to the two bench NICs. LED1/LED2 and `link bits: 11` in the poll output. If LED4 lights, suspect the lane (polarity, refclk, TX PLL) before the module.
3. **Loopback / forwarding tests** exactly as on the kit (`docs/flashing-and-testing.md` §5, `bench/`): beacon/sync on port 0, certificate capture, canonical forwarding 0→1. Port naming: SFP1 = port 0 (frontend), SFP2 = port 1 (compute).
4. **Optical modules** (if ever needed): rebuild the firmware with `MODE=basex`, swap the hex, rebuild the bitstream.

## 7. Verified vs. not

- ✓ PF_XCVR_REF_CLK, PF_TX_PLL (156.25 MHz → 5000 Mb/s) and PF_XCVR_ERM (2 lanes, PMA, 10-bit, 1250 Mb/s, CDR ref 156.25) parameter sets configure and generate cleanly (probe project on MPF300TS); generated port and bus-interface names are what `top.tcl` uses.
- ✓ Every `-pin_name` in `io_constraints.pdc` exists in Libero's FCG784 table with the expected function (`XCVR_2_TX0_P` = AC27, `GPIO167PB5` = E6, `HSIO57PB1` = AD18, ...).
- ✓ Firmware (offline checks, 2026-11-16): `make clean && make mpf200` reproduces the committed `iog_cdr_mpf200.hex` byte for byte (sha256 `3df8dd49…`, 7376 bytes, 462 records; `basex` and `mpf300` variants also link; no warnings with `-Wall -Wextra`). The ELF uses only RV32I + `divu`/`remu` while the Mi-V is configured RV32IMC with a fabric multiplier (`GEN_DECODE_RV32:3`, `GEN_MUL_TYPE:0`), entry = reset vector 0x80000000, code + 2 KB heap + 2 KB stack end at 0x80002CE0 inside the 36 KB TCM; the hex has valid checksums, is contiguous 0x0000–0x1CCF and equals `objcopy -O binary` of the ELF (the kit hex carries an extra type-05 start-address record that `RAM.cfg` does not need). `tse_mac_init()` is identical in value and order to the kit `main.c`; the MDIO sequence matches the kit's; the handbook confirms AN Advertisement `0x0001` for SGMII/TBI operation and `0x0020` for 1000BASE-X, control bits 12/9 and status bits 5/2; `top.tcl` loops each MAC's `MDO/MDOEN/MDI` back so PCS 18 is reachable only via MAC0 and PCS 19 only via MAC1, which is how the firmware pairs them. Host-side test: 245 checks × 2 builds, 0 failures (§3.5).
- ✓ Full dry run of the mpf200 design on the MPF300TS-FCG1152 stand-in die (Libero 2024.2, `INTERLOCK_TCL_OVERRIDE`, PF_XCVR_ERM 3.1.205, lane/refclk pads moved to that package's quad 2, all other pins unconstrained): create → constrain → synthesize (696 s) → place-and-route (1219 s, high effort + min-delay repair) → timing (264 s) all pass. **No timing violations in any corner**; worst slack 2.60 ns on the 80 MHz fabric clock, 3.23 / 3.42 ns on the two 125 MHz `RX_CLK_R`, 4.05 / 3.37 ns on the two `TX_CLK_R` (slow/LV/HT). Synthesized resources: 87,135 4LUT, 46,929 DFF, 364 LSRAM, 55 uSRAM, 4 math blocks, 2 transceiver lanes, 1 TX PLL — on an MPF200T that is ~45 % LUT, ~24 % DFF, 59 % LSRAM (616 blocks), so the smaller die fits with margin. This validates the Tcl wiring, IP configurations, clock constraints and implementability of the ported top, not the FCG784 pin placement itself.
- Lesson from that dry run, kept in `PF_XCVR_ERM_C0.tcl`: with the lane pads **unassigned**, place-and-route aborts at PRE_PLACEMENT with "Fail to create regional clock region for ... LANEn" — the regional interface clocks need the lane location, so never drop the transceiver pad constraints. Per-lane *Global* TX clocks are rejected by PRPF_029 (one global per quad); the configurator refuses TX and RX both Global; Regional/Regional (Microchip's own setting) is what builds.
- ✓ **Real MPF200T bitstream built** (2026-11-16, Libero SoC 2025.2, `./build.sh --board mpf200`, 2769 s wall): all twelve pinned IP versions downloaded and configured (CoreAPB3 4.2.100, COREJTAGDEBUG 4.0.100, CORESPI 5.2.104, CORETSE 3.2.119, CoreUARTapb 5.7.100, CORERESET_PF 2.3.100, MIV_RV32 3.0.100, PF_CCC 2.2.222, PF_INIT_MONITOR 2.0.308, PF_TX_PLL 2.0.304, PF_XCVR_REF_CLK 1.0.103, PF_XCVR_ERM 3.1.206); synthesis 764 s, place-and-route 1197 s, timing 279 s. **No timing violations in any corner** (max/min, slow/fast); the 80 MHz fabric domain achieves 12.335 ns vs. 12.5 ns required (0.17 ns margin on the STD-grade model — expect more on a -1 part), the four 125 MHz lane clocks achieve 4.5–5.4 ns vs. 8 ns. Every pin landed where `io_constraints.pdc` says (pin report: 26/26, XCVR pads and JTAG "Fixed", GPIO "Yes"). Utilization: 87,092 4LUT (45.3 %), 46,893 DFF (24.4 %), 364 LSRAM (59.1 %), 55 uSRAM, 4 math, 2 lanes, 1 TX PLL, 1 refclk, 9 globals. Output `gateware/Libero_Project/designer/top/export/top.job`, 7,864,979 bytes, sha256 `8ae2146c5f45220caaee21f2f0354c9bf10b8cb2034f6cf0b6df105cb1e053bd`; copy + build log kept in `artifacts/` (gitignored).
- ✗ Nothing has run on the board: transceiver link, SFP module behaviour (SGMII vs 1000BASE-X defaults of the chosen copper modules), UART channel assignment and LED polarity are all unverified on silicon.

## 8. Open items

- Flash `artifacts/top_mpf200_recomp_20261116_8ae2146c.job` (or rebuild) on the board and run the bring-up plan in §6.
- Buy/borrow two 1000BASE-T SFP modules (or 1G optics plus media converters) for the bench.
- If the board turns out to be a `-1` part with J18 on 1.05 V, consider `eval_part_speed {-1}` / `eval_die_voltage {1.05}` in `common.tcl` for timing margin (the STD build met timing with 0.17 ns on the 80 MHz domain).
- Confirm on the physical board: J18 jumper position (VDD), which cage carries the SFP1/SFP2 silkscreen (schematic nets vs. user-guide J2/J5), and which `ttyUSBn` is FTDI channel C.
- Firmware cosmetic: with a silent PCS (`0xFFFF`), `pcs_wait_an` and the poll loop print `link=1 an_done=1 lpa=0xFFFF` after the `NO RESPONSE` line; a `!= 0xFFFF` guard would make the log unambiguous. Deferred because it changes the committed hex and therefore needs a Libero rebuild — fold it into the next firmware change.
- Once the board is on the bench, record MPF200-specific flashing and bench notes here (§5/§6), not in `flashing-and-testing.md` / `bench/README.md`, which stay kit-centric.

## 9. When the MPF300 kit is back

Nothing needs undoing: `./build.sh` without `--board` has built the kit all along, and the kit's files were only moved into `gateware/src/boards/mpf300_eval/`. To drop the stand-in, delete `gateware/src/boards/mpf200_esb/`, `gateware/src/src_softconsole/{main_mpf200.c,iog_cdr_mpf200.hex}`, the `mpf200` block in `gateware/common.tcl`, the `mpf200` targets in the firmware Makefile and this document. What stays useful regardless: the board switch and `$<CORE>ver` indirection in `common.tcl`, the firmware Makefile, and the automatic `PF_INIT_MONITOR` 2.0.307 → 2.0.308 switch in `common.tcl` that the kit flow needs on a Libero 2025.x box.
