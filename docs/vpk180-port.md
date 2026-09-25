# Porting the interlock to the VPK180 (Versal Premium VP1802)

*Started 2026-09-24. Status: RTL shim + simulations done on the Hetzner box;
Vivado build waits on the link parameters and on the Versal-attached machine.*

The PolarFire build wraps the interlock core in Microchip-specific pieces:
CoreTSE soft MACs, IOD CDR SGMII lanes, a Mi-V running MDIO/PHY firmware, and
the VSC8575 copper PHY on the eval kit. On the VPK180 the network side is
completely different — optical modules in QSFP-DD cages on GTM transceivers,
hard MRMAC Ethernet blocks — while the interlock core itself does not care.
This document records what was learned about the board, what the port
changes, what still has to be decided, and how to bring it up.

## 1. What stays, what goes

| PolarFire (MPF300 eval kit) | VPK180 | Notes |
|---|---|---|
| `recomp_ilock_core` / `fabric_bridge` + everything below them (deframe, canon, commit, cert, SHA/HMAC, buffers) | **unchanged** | plain SystemVerilog, 32-bit CoreTSE-shaped bundles at 80 MHz |
| CoreTSE ×2 (1G MAC, MDIO master/slave) | MRMAC ×2 (hard 10–100GbE MAC+PCS+FEC), port 0 of each | one MRMAC per cage; AXI-Stream client interface |
| PF_IOD_CDR + PF_IOD_CDR_CCC (1.25 Gb/s SGMII lanes) | GTM quads via GT Quad Base (from the MRMAC example design) | GTM cannot run 1.25 Gb/s; see §2 |
| VSC8575 copper PHY, MDIO bus, `tse1_loopback` | optical module: I2C management (SFF-8024/CMIS), sideband GPIO | no PHY registers to program; module needs MODSEL/RESET/LPMODE handled |
| Mi-V + CoreUARTapb + CoreAPB3 + `iog_cdr.hex` firmware | MRMAC AXI-Lite init (example-design state machine or PS software); CIPS mandatory on Versal | the firmware's only data-path role was PHY/MAC bring-up |
| PF_CCC 80 MHz fabric clock | clk_wizard from the CIPS PL clock (80 MHz) | keeps `TIMER_END` and buffer sizing unchanged |
| `SSDetect`, debug LEDs, `pkt_counter`, `sticky_bit` | `pkt_counter`/`sticky_bit` reused on the 4 board LEDs | |
| Libero Tcl flow, `.job` | Vivado flow, `.pdi` (`gateware_vpk180/`) | |

New RTL, all in `gateware_vpk180/hdl/`, all covered by cocotb benches in `tb/`:

- `mrmac_axis_adapt` — the MRMAC's per-word pins (`tdata<N>[63:0]`,
  `tkeep_user<N>[10:0]`) ↔ one plain AXI-Stream of width W.
- `axis_pkt_fifo` — store-and-forward FIFO that drops whole packets on overflow
  and packets the MAC flagged bad. Needed because the MRMAC RX has **no
  tready** and the core cannot absorb line rate.
- CDC FIFOs — `xpm_fifo_axis` (independent clocks; packet mode on TX so the MAC
  never sees tvalid drop mid-packet). Sim stand-in: `sim_axis_cdc_fifo`.
- `axis_downsize` / `axis_upsize` — W ↔ 32-bit width conversion.
- `axis2tse` / `tse2axis` — AXI-Stream ↔ CoreTSE MAC-FIFO bundle
  (SOF/EOF/BYTEVALID). `axis2tse` looks one beat ahead so null last beats fold
  into the previous word.
- `mac_port_shim` — the above chained per port; `ilock_pl` — two shims + core
  + LEDs, the module that goes into the Vivado design.

## 2. Board and device facts (why the design looks like this)

- **All VPK180 cages are on GTM transceivers** (UG1582): QSFPDD1/2 (J1/J2) on
  GTM banks 208–211 (shared: with QSFPDD1 at 800G, QSFPDD2 is unusable; at
  ≤56G/lane both work), QSFPDD3/4 on GTM 111/112/117/118, QSFPDD5/6 on GTM
  121–124/221–224, OSFP on GTM 214–217, SFP-DD1–4 on GTM 109/110/115/116.
- **Cage-to-quad wiring, from the UG1582 Transceivers table** (verified against
  the device: bank 111 = `GTM_QUAD_X0Y9`, 112 = X0Y10, 117 = X0Y15, 118 = X0Y16,
  208–211 = X1Y6–X1Y9; refclk0 of quad X0Yn is site `GTM_REFCLK_X0Y<2n>`):

  | cage | lanes 1–4 | lanes 5–8 | refclk0 source (RC21008A) |
  |---|---|---|---|
  | QSFPDD3 | quad 111 (X0Y9) ch0–3, in order | quad 112 (X0Y10) ch0–3 | 111: GTCLK2_OUT2, 112: GTCLK2_OUT3 |
  | QSFPDD4 | quad 117 (X0Y15) ch0–3, in order | quad 118 (X0Y16) ch0–3 | 117: GTCLK1_OUT7, 118: GTCLK2_OUT7 |
  | QSFPDD1 | 208 ch1, 208 ch3, 209 ch0, 209 ch2 | 210 ch0, 210 ch3, 211 ch0, 211 ch3 | 208–211: GTCLK1_OUT0–3 |
  | QSFPDD2 | 208 ch0, 208 ch2, 209 ch1, 209 ch3 | 210 ch1, 210 ch2, 211 ch1, 211 ch2 | (shared quads with QSFPDD1) |

  **What each cage can run** (UG1582 cage pages + Transceivers table; a
  112 Gb/s PAM4 lane occupies a whole GTM *dual*, i.e. two channels, per
  AM017, and the 100GAUI-1 IP config indeed takes two channels):

  | cage | per-lane wiring | max per lane | 400G with 100G-lane optics (DR8/DR4)? | 8×50G modules (400GAUI-8)? |
  |---|---|---|---|---|
  | QSFPDD1 | every lane on its own dual, quads 208–211 (odd channels) | 112G, 800G total | **yes**: lanes 1–4 = quads X1Y6+X1Y7 (DCMAC_X1Y1 adjacent) | yes |
  | QSFPDD2 | even channels of the same quads (dual partners of QSFPDD1) | 56G while QSFPDD1 is idle at 112G | no (conflicts with QSFPDD1) | yes |
  | QSFPDD3 | lanes 1–4 on the four channels of quad 111, 5–8 on quad 112 | 56G | **no**: a 4-lane group needs 4 duals | yes (400GAUI-8, the board's intent) |
  | QSFPDD4 | same pattern on quads 117/118 | 56G | no | yes |
  | QSFPDD5 | lanes on ch1/ch2 of quads 121–124 (own duals) | 112G, 800G total | **yes**: lanes 1–4 = quads X0Y19+X0Y20 (DCMAC_X0Y6 adjacent) | yes |
  | QSFPDD6 | odd lanes share duals with QSFPDD5, even lanes on quads 221–224 | 112G only while QSFPDD5 is idle | not together with QSFPDD5 | yes |
  | OSFP | quads 214–217, rated 112G | 112G | yes | yes |

  Refclks: quads 208–211 from RC21008A GTCLK1_OUT0–3, quads 121–124 from the
  8A34001 Q8 (default design 156.25 MHz, measured 156.251), quads 221–224
  from 8A34001 Q9. So with the FS DR8 modules the two 400G ports are
  **QSFPDD1 and QSFPDD5**. With 100G QSFP28 (25G NRZ) or 400GAUI-8 optics any
  cage works and QSFPDD3/4 next to MRMAC X0Y2/X0Y4 are the convenient pair. MRMAC sites on this
  device: X0Y2/3 (clock region X0Y6), X0Y4/5 (X0Y9), X0Y6/7 (X0Y12), X1Y0
  (X9Y1, no example-design quad), X1Y1 (X9Y4). The example design offers only
  the adjacent quad per site (X0Y8, X0Y14, X0Y20, X1Y4), one region away from
  the cage quads, so the GT is relocated after generation.
- **GTM line rates start around 9.5 Gb/s NRZ** and go to 112 Gb/s PAM4. So no
  SGMII/1000BASE-X and no 1G modules. Every option is 10GbE or faster, which
  is why the MAC is the MRMAC (10/25/40/50/100GbE) rather than a 1G TEMAC.
- **What is actually plugged in (read 2026-09-24 via the system controller,
  `sc_app -c getSFP`)**: two FS **QDD-DR8-800G** modules, in QSFPDD1 and
  QSFPDD3. 800G DR8 = eight 100G PAM4 optical lanes (100GBASE-DR per lane),
  host side 800GAUI-8 (8 × 106.25 Gb/s PAM4). No NRZ or 50G-per-lane modes,
  so per interlock port the link is **one lane at 106.25 Gb/s: MRMAC
  100GAUI-1, RS(544,514) FEC**, and the far end must be 100G-DR (or a DR4/DR8
  port broken out to 100G lanes). Single-lane links also make QSFPDD1's
  interleaved wiring usable (lane 1 = quad 208 ch1), but channel 0 of a quad
  next to an MRMAC is simpler, hence the QSFPDD3/QSFPDD4 recommendation.
- **System controller** (ZU4 running PetaLinux, console on the FT4232H's 4th
  channel, login petalinux/petalinux, `sc_app`): reads module EEPROMs
  (`getSFP`), programs/measures the GTM refclks (`getclock`,
  `getmeasuredclock`: both 156.25 MHz), drives the QSFP-DD MODSELL lines via
  the TCA6416A expander (`getioexp`/`setdirioexp`), and can even load a PDI
  into the Versal (`loadPDI`). `gateware_vpk180/tools/sc_console.py` scripts
  it. Board definition: `/usr/share/system-controller-app/board/VPK180.json`
  (QSFPDD3/4 on `/dev/i2c-18` at 0x50, gated by MODSELL).
- **GTM reference clocks**: two Renesas RC21008A programmable generators (U298,
  U299), default 156.25 MHz LVDS, I2C address 0x09. 156.25 MHz is the standard
  Ethernet refclk and is in the MRMAC's allowed list.
- **QSFP-DD sideband**: module I2C on the I2C1 bus through a TCA9548 mux (U35);
  MODSELL via a U233 GPIO expander; RESETL, MODPRSL, INTL and INITMODE (the
  QSFP-DD LPMODE) on Versal bank 711 pins. An optical module must see MODSELL
  low (to talk I2C), RESETL high and INITMODE low (high-power) before it will
  bring up its lasers; passive DACs need none of this.
- **Vivado licensing**: the VP1802 is not in the free ML Standard edition. The
  box here has ML Standard with only Zynq-7000 device files (see
  `/root/fli/vivado/README.md`), so the Versal machine is where synthesis
  happens. The local install still carries the full IP catalog, which is
  where the MRMAC parameter names/values in `params.tcl` were taken from.
- **MRMAC AXI-Stream shape** (PG314): non-segmented mode is available at every
  rate — 64 b for 10/25GbE, 128 b for 40/50GbE, 384 b for 100GbE — on the
  "Independent" AXI clock, which may be any frequency ≥ half the core clock;
  rx/tx_axi_clk may be the same clock. RX has no tready. tkeep is only defined
  on the tlast beat; bit 8 of each `tkeep_user` word flags a bad frame that
  must be discarded (the MAC does not drop it for you). In the block-design
  view, `axis_rx_port0`/`axis_tx_port0` carry only tvalid/tlast(/tready); the
  data words are ordinary pins.

## 3. Architecture

```
 cage 0 (frontend)                                                   cage 1 (compute)
   │ 4/2/1 lanes                                                          │
 GTM quad ── MRMAC #0 ─ rx: tdata/tkeep_user, tvalid, tlast ─┐    ┌─ MRMAC #1 ── GTM quad
                        tx: ... + tready                     │    │
                                             mrmac_axis_adapt│    │mrmac_axis_adapt
                        ┌─ mac_rx_clk ─ axis_pkt_fifo ─ CDC ─┘    └─ ...
                        │          core_clk (80 MHz): axis_downsize ─ axis2tse
                        │                                     │
                        │            recomp_ilock_core / fabric_bridge  (unchanged)
                        │                                     │
                        └─ mac_tx_clk ◄ CDC(packet) ◄ axis_upsize ◄ tse2axis
```

### 3.1 Throughput and drop semantics

The core still moves 32 bits per 80 MHz cycle (≈2.5 Gb/s ceiling), far below
any GTM link rate. Traffic beyond what the core drains is dropped **whole
packets at a time, in the MAC clock domain, before the core sees anything**
(`axis_pkt_fifo`). For the interlock this is indistinguishable from loss on
the wire: dropped frames are never committed, never forwarded, never counted.
The bench traffic (tens of Mb/s per direction) is nowhere near the limit; if
higher throughput is wanted later, the core clock can go up on Versal
(`TIMER_END` scales with it) and/or the datapath widened — separate work.

The RX drop FIFO defaults to 1024 beats (two maximum frames at 64 b; many more
at 384 b). LED 3 latches if anything was ever dropped, so a silent link with
LED 3 lit means "overloaded or bad frames", not "no traffic".

### 3.2 Byte order and framing — why `eth_deframe`/`eth_reframe` need no change

Both the CoreTSE bundle and the MRMAC stream put the first wire byte of a
beat in bits [7:0]. `eth_deframe` expects the FCS **present** in the RX stream
(it drops it by LENGTH) and `eth_reframe` **computes and appends** the FCS
itself. So the MRMAC must be configured for FCS pass-through:

| MRMAC control | value | effect |
|---|---|---|
| `ctl_rx_delete_fcs` | 0 | leave the FCS on RX frames |
| `ctl_rx_ignore_fcs` | 0 | still check it; bad frames get `tkeep_user[8]` and the shim drops them |
| `ctl_tx_fcs_ins_enable` | 0 | do not add a second FCS on TX |
| `ctl_tx_ignore_fcs` | 0 | (checks the FCS we append; set 1 only for debugging) |
| preamble | default (MAC-generated) | custom preamble mode off |
| pause / PFC | off | matches the PolarFire build (no flow control) |
| min/max frame | defaults (64 / MTU 1518+) | canonical packets stay ≤ 1500 payload |

These are AXI-Lite register bits (PG314 `CONFIGURATION_RX_REG1_<port>`,
`CONFIGURATION_TX_REG1_<port>`); they are set wherever the bring-up sequence
lives (example-design state machine or PS software).

### 3.3 Control plane

Versal designs always contain the CIPS block (it boots the device). The
smallest bring-up keeps the MRMAC example design's own AXI-Lite configuration
logic and adds nothing else; module sideband pins are tied to their
enable-state in the XDC. The step after that is a small program on the APU
(baremetal or Linux) that (a) configures the module via I2C1/TCA9548, (b)
performs the MRMAC register sequence with the FCS settings above, (c) reports
link status over the UART — the role the Mi-V firmware played on PolarFire.

## 4. Parameters to pin down before synthesis (`gateware_vpk180/params.tcl`)

| Parameter | How to determine | Where the allowed values come from |
|---|---|---|
| optics type → `GT_SIG_MODE`, lanes | module label / EEPROM (SFF-8024 identifier via I2C, or the vendor part number) | 100G SR4/LR4/CWDM4 → NRZ ×4; 400G SR8 → PAM4 ×8 (breakout 4×100G = 2 lanes each); 400G DR4/FR4 → PAM4 ×4 (100GAUI-1 per 100G) |
| peer → rate, FEC | what the NIC/switch on the far end is set to (`ethtool`, switch config) | 100GbE CAUI-4 needs RS-FEC KR4 (528,514); PAM4 modes need KP4 (544,514) |
| `MRMAC_PRESET` | combination of the two rows above | MRMAC v3.2 preset list (also in the IP GUI) |
| `MRMAC_AXIS_IF`, `MAC_AXIS_W` | by rate: 64 b (10/25G), 128 b (40/50G), 384 b (100G) | must match `ilock_pl` `MAC_AXIS_W` |
| `GT_REFCLK_MHZ` | 156.25 unless the system controller was reprogrammed | RC21008A U298/U299 |
| cages → `PORTx_GT_QUAD`, `PORTx_GT_REFCLK`, `MRMACx_LOC` | which cages are cabled; site names from the VPK180 board XDC / `get_sites` | UG1582 cage↔bank table in §2 |
| `Px_WORD_BASE` | which `tdata<N>` words port 0 of the configured block uses | read off the generated example design's packet generator |
| `ILOCK_TOP_KIND`, `ILOCK_TIMER_END`, `ILOCK_BKTS_PER_CERT` | same choices as the PolarFire build (`TOP={recomp,prod}`, `BUCKET_MS`) | |

Both cages must run the same configuration only for convenience — the RTL
allows a different `MAC_AXIS_W` per port if that ever becomes necessary.

## 5. Flow

**Here (any Linux box):** RTL + simulation.

```
make test-vpk180 [W=64|128|384]    # cocotb/Icarus benches for every shim block and the composed port
make lint-vpk180                   # Verilator -Wall, ilock_pl with either core
```

**Versal machine:** `gateware_vpk180/build.sh exdes → integrate → build →
program`. `gen_exdes.tcl` configures one MRMAC from `params.tcl` and opens
its example design (GT quad, clocks, resets, AXI-Lite bring-up, packet
generator/monitor). `integrate.tcl` adds the interlock sources and lists the
example hierarchy; the generator/monitor is then replaced by `ilock_pl` by
hand (the example design's Tcl/Verilog templates are encrypted in the IP
catalog, so this step could not be scripted blind). `build.tcl` runs
synth/impl and copies the `.pdi` to `build/out/`.

**Two-port link test (done 2026-09-24, passes):** `build.sh dual-link →
dual-sw → dual-run [secs]`. `scripts/dual_link_build.tcl` works inside the
example project: `copy_ip` of the MRMAC for port 1 relocated to `MRMAC_X1Y1`
(the IP insists on `MRMAC_LOCATION_C0` + `MRMAC_EXDES_GT_*_LOCATION_C0` being
set together), a `copy_ip` of the GT wizard moved to the quad's channels 2,3
for port 0 (the wizard's lane-map properties are locked; what works is one
atomic `set_property -dict` of `QUAD0_PROT0_{TX,RX}{0..3}_EN`,
`QUAD0_PROT0_{TX,RX}MSTCLK = TX2/RX2` and `QUAD0_{TX,RX}{2,3}_OUTCLK_EN`),
then `tools/gen_dual_link.py` derives the two wrappers (`mrmac_p0_exdes`,
`mrmac_p1_exdes`), the top (`mrmac_dual_top`: both wrappers on the example's
CIPS block, port 1 on the spare AXI masters `M00_AXI_1` = 0xA4A00000 and
`M00_AXI_2` = 0xA4B00000) and the XDC (per-instance quad/refclk LOCs). The PS
program is `sw/mrmac_dual_test.c` on top of the example's C (base address made
a variable): bring both MACs up, shared GT reset, wait for RX alignment on
both, trigger the example PRBS generators, compare each side's TX counters with
the other side's RX counters. Outputs land in `build/dual_link/`.

**In-line pass-through (step 2 of §6):** `build.sh inline-build → inline-sw →
inline-run`. Four MRMACs in the same shell (`scripts/inline_build.tcl`,
`tools/gen_inline.py`):

| port | role | cage / module lane | GT | MRMAC |
|---|---|---|---|---|
| 0 | pass-through | QSFPDD3 lane 1 ("1-4" port, position 1) | quad 111 = X0Y9 ch0 | X0Y2 |
| 1 | pass-through | QSFPDD1 lane 3 ("1-4" port, position 3) | quad 209 = X1Y7 ch0 | X1Y1 |
| 2 | traffic endpoint | QSFPDD3 lane 5 ("5-8" port, position 1) | quad 112 = X0Y10 ch0 | X0Y3 |
| 3 | traffic endpoint | QSFPDD4 lane 7 ("5-8" port, position 3) | quad 118 = X0Y16 ch2 | X0Y5 |

Fibre 1 is a loop between the two MPO ports of the cage 3 module (endpoint
lane 5 ↔ pass-through lane 1, both position 1); fibre 2 runs from cage 4's
"5-8" port to cage 1's "1-4" port (lane 7 ↔ lane 3, both position 3). Two
female Type B MPO-12 cords. Why this odd layout: cage 4's "1-4" port (quad
117) is barred at 106G on both usable channels by the hard DRC GTMXTLK-2 (AR
35326, package crosstalk restriction; the check cannot be waived), and cage 2
would need MRMAC_X1Y0, four clock regions from its quad. Quads 111, 112, 118
and 209 are clean.

Each wrapper gains an external client interface (`ext_sel`: TX from the
external client instead of the example generator; RX exposed next to the
monitor); `ilock_pl` with `TOP_KIND=2` sits between ports 0 and 1 and a small
AXI-Lite block (`hdl/passthru_ctl_axil.sv`, 0xA4D00000) selects `ext_sel` per
port and crossover vs reflect (`pt_xover`). Reflect (a pass-through port
echoes its own RX through the whole shim) makes the datapath testable over a
single fibre: the far end's generator is the source and its monitor the sink.
The PS program (`sw/mrmac_inline_test.c`) infers which fibres are present from
RX alignment, then runs plain link checks, reflect at each pass-through port,
and the crossover E0 → P0 → P1 → E1 when both fibres exist.

Synthesis lesson: the shim's `axis_pkt_fifo` storage had to move out of the
async-reset process (Vivado will not map an async-reset memory to block RAM
and refused to dissolve 443 kbit into registers); the array and its registered
read now live in a reset-free clocked process. The same applied to the core's
`batch_buffer` (2 × 2.6 Mbit banks): its bank writes and reads moved to a
reset-free process driven by the same FSM conditions, with the bank select
latched (`rd_sel_q`) so `rd_data` keeps its exact value and timing; the
PolarFire build is unaffected functionally.

**Result on hardware (2026-09-25):** with cages 1 and 3 on the one fibre, the
pass-through shim passed in both reflect directions (28 frames sent → 28
through the shim → 28 back, no drops), on top of the plain link check.

**PS-driven traffic and the runtime core switch (`INLINE_TOPO=psdirect`):**
`hdl/ps_frame_port.sv` is a frame injector/capture with an MRMAC-client pin
interface and an AXI-Lite window (ID, TX_LEN/COUNT/CTL/STAT/SENT, RX_CTL/STAT/
COUNT, a 4 kB TX buffer at 0x1000 and a 4 kB RX buffer at 0x2000; see the
file header). The injector path is buffer → 32-bit stream → up-size →
packet-mode clock crossing, so frames reach the consumer gap-free; the capture
path is adapter → drop FIFO → clock crossing → down-size → buffer, whole
frames only, errored frames dropped. Two instances: A behind the cage 3 MRMAC
(replacing the example generator when `ext_sel[0]` = 1, capturing the MAC's
RX), B attached straight to `ilock_pl` port 0 as a virtual MAC on the 100 MHz
PL clock. That gives the full crossover with one fibre:

```
[PS frames A] <-> [MRMAC cage 3] ==fibre== [MRMAC cage 1] <-> [ilock_pl p1 -> p0] <-> [PS frames B]
```

`ilock_pl` gained `RUNTIME_BYPASS`: with a core (`TOP_KIND` 0/1) the
pass-through switch is built as well and `mode_core` (CTL bit 9) picks the
owner of the TSE bundles; the core is held in reset while bypassed, so
switching it in always starts from a clean state. `TIMER_END` is scaled for the
100 MHz core clock (9,999,999). Benches: `tb/test_ps_frame_port` (inject,
capture, overflow), `tb/test_ilock_pl_bypass` (fabric bridge + switch: bypass,
core, bypass). `sw/mrmac_psdirect_test.c` injects frames of several sizes at B
and compares them byte for byte at A and vice versa in pass-through mode, then
offers the same frames with the recomp core switched in (informational).
AXI: frame port A 0xA4B00000, B 0xA4C00000, CTL 0xA4D00000.

**Result on hardware (2026-09-25 08:00, `build/console_075809.log`):** every
frame size tried (60, 64, 65, 200, 999, 1500, 1514 bytes) arrived byte-identical
in both directions through the full crossover (B → ilock_pl port 0 RX chain →
port 1 TX chain → cage 1 MAC → fibre → cage 3 MAC → A, and the reverse), MAC
counters 7/7/7/7, drop flag clear; timing met (WNS +0.001 ns). With the recomp
core switched in, a plain 200-byte frame from the frontend side (B) is gated as
expected and the enclosure side (A) receives an 82-byte protocol frame from the
core instead: the core is live in the path and can be toggled at runtime.

Build note: Vivado 2025.2 aborted three times in synthesis ("IO Insertion",
right after stitching unchanged partitions) once the interlock core was in the
project; the crashed runs were incremental synthesis runs reading the previous
run's auto-incremental checkpoint. `inline_build.tcl` now disables
`AUTO_INCREMENTAL_CHECKPOINT` and runs synthesis with 8 threads; clean runs
pass. Placement constraints live in `inline_loc.xdc`, used in implementation
only, and are verified after implementation against `params.tcl`.

## 6. Bring-up sequence

1. **Link only, no interlock**: build the untouched MRMAC example design for
   the chosen preset, program, confirm `stat_rx_status`/link on both cages
   against the peer. Module sideband (MODSEL low, RESETL high, INITMODE low)
   and refclk (156.25 MHz) are the usual suspects if the link never comes up.
2. **Loopback through `ilock_pl`** with the core replaced by a wire
   (`tse0_mrx → tse1_mtx`, `tse1_mrx → tse0_mtx`): frames sent into cage 0
   appear at cage 1 unchanged. Validates the shim, FCS pass-through and both
   clock crossings on silicon.
3. **Interlock core in**: same tests as the PolarFire bench
   (`docs/flashing-and-testing.md` §5) with the NICs replaced by whatever the
   peers are — beacon/sync on port 0, certificate capture, canonical-packet
   forwarding. LED 3 must stay dark at bench rates.
4. **Control plane on the APU** (optional for the prototype): I2C module
   management, MRMAC register init, UART status — replacing the example
   design's state machine.

## 6b. 400G path (DCMAC), forced by the DR8 optics

The FS QDD-DR8-800G modules only present 100G PAM4 lanes in groups of four,
so with them each interlock port is one 400GE link (400GAUI-4: 4 × 106.25
Gb/s, RS-544 CL119), on the DCMAC rather than the MRMAC, and only in the
cages whose lanes each own a GTM dual: **QSFPDD1** (quads 208/209 =
X1Y6/X1Y7, DCMAC_X1Y1 adjacent) and **QSFPDD5** (quads 121/122 =
X0Y19/X0Y20, DCMAC_X0Y6 adjacent). `params.tcl` carries this as the
`PORTA_*`/`PORTB_*` block.

What the DCMAC IP (v3.1) needs: `MAC_PORT0_CONFIG_C0 = 400GAUI-4` (the
default 400GAUI-8 is eight 53G lanes and silently stays if only
`GT_MODE_C0` is changed), `DATA_RATE_CFG_0 = 400G`, RS(544) CL119, GTM,
156.25 MHz. The example design still adds a 200G port on a third quad
(`DATA_RATE_CFG_4` reverts to 200G), which the relocation parks on quad
210. A 106G lane occupies two channels (half-density mode, `HD_EN 2`,
"PROT_DUAL_OCCUPIED BOTH"); which channel of the pair owns the serial
pins is not visible in the configuration, so the first programming run
reads the module's per-lane host-signal flags to confirm lanes 1-4 land on
QSFPDD1 and not on QSFPDD2's pins.

Flow (`gateware_vpk180/build.sh`): `dcmac-exdes` → `dcmac-test` (relocated
build, PDI + XSA) → `dcmac-sw` (platform + the example's four-file PS
program) → `link-run <pdi> <elf>`. The shipped PS program selects near-end
PCS loopback in `dcmac_exdes_test_config.c` (GT control word 0x65921900);
`build/dcmac_link_test/ext/` holds the external-link variant.

**Result of the first 400G run (2026-09-24, QSFPDD1 module, DCMAC on
X1Y1 relocated to quads X1Y6/X1Y7/X1Y8):** timing met (WNS +0.001 ns), the
example's PS program reports port 0 "RX achieved alignment" in near-end PCS
loopback, and with the four lanes driving, the FS DR8 (woken via CMIS byte
26) turned all eight lasers on (1.3–1.7 mW per lane) and reported host CDR
lock on module lanes 3, 4, 5 and 6. Lanes 1 and 2 did not lock: those sit on
the odd channels of quad 208, and in half-density 106G mode the transceiver
placed each lane on the even channel of its pair (QSFPDD2's pins). Every
4-lane group on this board mixes parities (208: odd/odd, 209: even/even for
QSFPDD1 lanes 1–4; QSFPDD5 needs odd+even within each quad), so the
"Odd/Even Active Lanes" choice the standalone Versal Transceivers Wizard
offers for GTM half-density mode has to be reproduced inside the
DCMAC-generated wizard (`dcmac_0_gtwiz_versal_0`). Not found yet: it is not a
preset, not a named parameter, the child quad IPs are scoped (read-only), and
a swapped `INTF0_CHANNEL_MAP` is silently reverted. Next step: read the
option's Tcl from the wizard GUI (journal) and apply it here.

Fibers (resolved 2026-09-24 afternoon): the modules are the dual-MPO-12
variant (CMIS connector byte 0x0C = MPO 1x12; port "1-4" = lanes 1-4, port
"5-8" = lanes 5-8). The cable that works is the FS 12F MPO(F)-MPO(F)/SM/2M
Type B APC trunk between the two "1-4" ports (the 1 m male-male one cannot
seat in the pinned module ports). With it, ~1 mW arrives on lanes 1-4 at both
ends, lane n to lane n. Note the module squelches the optical output of every
lane without host CDR lock (`OutputStatusTx` bit clear) even though its TX
power monitor still reads ~1.4 mW, so cabling checks need an FPGA-driven lane.

The interlock side for 400G is a later milestone: the DCMAC client stream
is segmented (a beat can hold the end of one frame and the start of the
next), so `mac_port_shim` needs a segmented-to-packet stage in front of the
existing chain; everything from the drop FIFO onward stays.

## 7. Verified vs. not

### Bring-up log

- **2026-09-24, monster + VPK180 (JTAG serial 442606191918A).** Step 1 of §6
  done: the untouched MRMAC example design for **1x100GE 100GAUI-1 Wide,
  MAC+PCS+FEC (RS-544), PAM4, 156.25 MHz refclk**, relocated to
  `GTM_QUAD_X0Y9` / `GTM_REFCLK_X0Y18` (QSFPDD3 lane 1), built with timing
  met (WNS +0.23 ns) and ran on the board: **RX aligned, 28/28 PRBS frames,
  "1x100GE Test Pass" — in near-end PCS loopback**, which is what the
  generated PS program actually selects (its GT control word 0x4B009F02 has
  loopback field [14:12] = 001 despite the "External" comment). With the true
  external word (0x4B000F02, `build/link_test/ext/`) RX does not align: the
  module reports RX LOS on every lane, i.e. no light arrives on QSFPDD3 lane 1
  from the fiber. So the PL/MAC/FEC/GT chain is proven at 106.25 Gb/s; the
  optical path is not, pending a live 100G-DR peer or a fiber loopback.
- Fixes on the way (all in `gateware_vpk180/scripts` and `tools`): the
  example C's GPIO base macros expand to `XPAR_*_DEVICE_ID` (0/1/2) with this
  BSP and cause a data abort at address 1 (patched to `*_BASEADDR`);
  `build_ps_app.sh` compiles the PS program directly against the platform
  BSP because the Vitis workspace builder only works in the session that
  created it; `link_run.sh` is the reprogram ritual (system-controller POR,
  `hw_server` restart, JTAG-mux release via `getgpio`, xsdb program/run,
  console capture) — a running PLM rejects a new PDI over JTAG otherwise.
- Module handling (system controller, `tools/sc_console.py`): the DR8 modules
  come up in ModuleLowPwr because INITMODE/LPMode on bank 711 is undriven;
  clearing CMIS `LowPwrAllowRequestHW` (byte 26) puts them in ModuleReady.
  The module accepts only its factory configuration (AppSel 3, data paths
  {lanes 1-4}, {lanes 5-8}: re-applying it returns ConfigSuccess) and rejects
  every other application code with ConfigRejectedInvalidDataPath. That
  turned out not to matter: **TX output is enabled per lane as soon as that
  lane's host input locks**, whatever the rest of the group does. Verified
  2026-09-24 with the single-lane MRMAC 100GAUI-1 design on QSFPDD3 (lane 1
  = quad 111 ch0): host CDR lock on lane 1 (`TxCDRLOL` 0xfe), `OutputStatusTx`
  0x01, 1.37 mW out of lane 1, group state still "DPInitialized". The earlier
  "never activates" reading was a lock problem (the transmitter had been
  reprogrammed away). The same was seen on QSFPDD1 in the 400G run (lanes
  3–6 enabled individually). So **one 100G-DR lane per DR8 module is usable
  with the MRMAC path** as long as the peer presents 100GBASE-DR on that
  fiber pair (a 100G-DR port, or a DR4/DR8 port broken out); the other
  seven lanes can stay TX-disabled. RX-side enable on incoming light is the
  remaining thing to see, which needs a peer or an MPO loopback.
- **2026-09-24 afternoon, fibre + two-port link.** Cages 1-4 all hold DR8s
  (`tools/qsfp_lights.py` reads state/LOS/optical power per lane via the SC
  console). Every earlier "no light" reading was explained by the cable
  (male-male, cannot seat) and by lanes without host lock being squelched.
  With the female Type B cable and the 400G bitstream driving cage 1 lanes
  3-6, cage 3 received ~1 mW on lanes 1-4 and locked on lanes 3,4 — the
  first light ever seen. The two-MRMAC design (lane 3 both ends, see §5) then
  built in ~35 min and passed the packet test in both directions on the first
  run.


- ✓ Shim RTL lint-clean (Verilator -Wall) and simulated with cocotb/Icarus at
  64-, 128- and 384-bit widths (16 tests per width): data integrity with random gaps/stalls, tkeep/
  tlast placement, null-last-beat handling, errored-frame and overflow drops,
  in-order survival, TX "tvalid never drops mid-packet".
- ✓ `ilock_pl` elaborates with both interlock cores.
- ✓ MRMAC example design (100GAUI-1) synthesized, implemented and run on the
  VPK180 from `monster` (see the bring-up log above); the interlock RTL has
  not been synthesized for the VP1802 yet.
- ✓ **Throughput through the workload interlock, request direction** (2026-09-25
  09:14, `sw/mrmac_bw_test.c`, `build/console_091355.log`): frames stamped by
  the PS (ID and bucket patched per frame, bucket from the sync packets read
  in the same loop), forwarded frames counted on cage 1's MAC and received on
  cage 3's; 20,000 frames per run.

  | frame | sender rate | guard | lost |
  |---|---|---|---|
  | 1518 B | 1.30 Gb/s (PS limit) | none | 205 (1.0 %, one per bucket boundary) |
  | 1518 B | 1.24 Gb/s | 25 µs before the tick | 0 |
  | 1518 B | 1.01 Gb/s | 25 µs | 0 |
  | 516 B | 0.96 Gb/s (PS limit) | none | 104 (0.5 %) |
  | 516 B | 0.88 Gb/s | 25 µs | 0 |
  | 132 B | 0.47 Gb/s (PS limit) | none | 57 (0.3 %) |
  | 132 B | 0.41 Gb/s | 25 µs | 0 |

  The unguarded loss is the frame in flight at the bucket tick (stamped with
  the closing bucket, checked in the next): a real frontend must hold off
  before the boundary the same way; 25 µs covers the frame port's packet
  buffering plus the tick-prediction error. The pass-through shell forwards
  the injector's 1.6 Gb/s ceiling lossless at every size. Not measured yet:
  the response direction (needs a receive counter on the direct-attached frame
  port) and the core's own ceiling (2.56 Gb/s bank limit / 3.2 Gb/s path; needs
  hardware ID/bucket stamping to exceed the PS rate). A72 → PL AXI-Lite: 129 ns
  per write, 249 ns per read. Cage 3 counts core frames as length errors
  (double FCS: reframer + MAC), still delivered; to be cleaned up by turning
  off FCS insertion on the MAC in core mode (`TX_REG1` bit 1 is not that bit).
- ✓ **Workload interlock live on the fibre, 1 ms buckets** (2026-09-25 08:47,
  `build/console_084702.log`): with `fabric_bridge` switched in at t=0 the
  frontend port sees a sync packet every bucket (2475 in 2.5 s, buckets
  0..2474, none out of sequence) and the compute side the same over the
  fibre; certificates (version 6, device 66, bkt_start 0/1000, bkt_num 1000)
  every 1000 buckets; canonical requests (PLD_LEN 64..1436) stamped with the
  current bucket are forwarded verbatim to the compute side about 1 ms after
  send, responses the same way back, a stale-bucket request is dropped, no
  drops anywhere. Senders must take the bucket from a *fresh* sync: with
  queued capture a port's backlog holds hundreds of old syncs (drain first).
  Timing of that image: WNS −0.067 ns on port 1's MRMAC↔GT receive path
  (the two-clock-region placement); works on hardware, to be tightened.
- ✓ **PS-direct in-line crossover with the interlock shell passes** (2026-09-25
  08:00): PS frame port B → `ilock_pl` (pass-through) → cage 1 → fibre → cage 3
  → PS frame port A and back, 7 sizes × 2 directions byte-identical, no drops;
  recomp core switchable at runtime and live in the path (§5).
- ✓ **Pass-through shim on hardware passes** (2026-09-25, `inline-build`
  with `INLINE_NPORTS=2`, the same two ports plus `ilock_pl` TOP_KIND=2 and
  the control register): reflect at cage 3 with cage 1 generating and the
  mirror case, 28 frames sent → 28 into the shim → 28 out → 28 back, drop
  flag clear, plus the plain link check; timing met (WNS +0.001 ns).
  `build/console_065059.log`.
- ✓ **Two-MRMAC 100GAUI-1 link over the fibre passes** (2026-09-24 18:00):
  QSFPDD3 lane 3 (MRMAC_X0Y2, quad X0Y9 channels 2,3) ↔ QSFPDD1 lane 3
  (MRMAC_X1Y1, quad X1Y7 channels 0,1), RS-544 FEC, external mode. Both RX
  aligned on the first attempt; PRBS bursts of 28-29 packets / ~8.6 kB per
  trigger counted identically at the far end in both directions over three
  rounds (`build/console_175721.log`). WNS −0.06 ns on an unused 50G
  generator path, accepted for the test.
- ✗ Which `tdata<N>` words a port uses, the example design's exact module
  names, and the CIPS/GT automation details are to be confirmed on the Versal
  machine.
- ✗ The XPM FIFO instantiations compile in Vivado's XPM library only; the
  benches use the behavioural stand-in.

## 8. Open items

- Link parameters are settled for the board-to-board test: 100GAUI-1, RS-544,
  module lane 3 at both ends, cages 1 and 3 (`params.tcl`). Why lane 3: a 106G
  half-density lane lives on the even channel of a GT dual, and cage 1's
  lanes 1,2 sit on the odd channels of quad 208, so lane 3 (quad 209 ch0) is
  the first lane cage 1 can drive; the Type B cable maps lane n to lane n.
- Core clock: decided 2026-09-25 — the core runs on the 100 MHz PL clock.
  Apart from `TIMER_END` (parameter) the only cycle-count constant in the core
  RTL is the batch buffer's 2000-cycle grace period, a pipeline-drain margin
  independent of frequency; so `TIMER_END = 99_999` (1 ms buckets),
  `BKTS_PER_CERT = 1000` (one certificate per second), no extra MMCM, and the
  32-bit core path carries 3.2 Gb/s instead of the PolarFire's 2.56.
- The PS-direct build (`INLINE_TOPO=psdirect`) carries the **workload
  interlock** (`fabric_bridge`, TOP_KIND 1) with the runtime bypass;
  `INLINE_CORE=0` selects the recomputation core instead. Bring-up program:
  `sw/mrmac_ilock_test.c` (sync/certificate cadence on both sides from the
  first tick, canonical requests and responses across, stale-bucket drop);
  `sw/mrmac_recomp_test.c` is the recomputation-core variant (100 ms buckets).
- Whether the eventual peers can run a 100GbE mode that the optics support;
  a DGX Spark's ConnectX-7 QSFP112 ports do 200G natively and 25/50/100G lane
  modes, but NVIDIA lists port splitting as unvalidated on the Spark.
- Timing: the cage 1 MRMAC (X1Y1, clock region X9Y4) sits two regions from
  its quad (X1Y7, X9Y6); the link test met timing apart from an example-only
  path, but keep an eye on it when the interlock is added.
- Later: raise `core_clk` and widen the core datapath if line-rate
  attestation is ever needed.
