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
- **GTM line rates start around 9.5 Gb/s NRZ** and go to 112 Gb/s PAM4. So no
  SGMII/1000BASE-X and no 1G modules. Every option is 10GbE or faster, which
  is why the MAC is the MRMAC (10/25/40/50/100GbE) rather than a 1G TEMAC.
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

The second MRMAC (port 1) is a copy of the first at `MRMAC1_LOC` /
`PORT1_GT_QUAD`; alternatively generate the example design for a two-block
configuration if the IP offers it for the chosen preset.

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

## 7. Verified vs. not

- ✓ Shim RTL lint-clean (Verilator -Wall) and simulated with cocotb/Icarus at
  64-, 128- and 384-bit widths (16 tests per width): data integrity with random gaps/stalls, tkeep/
  tlast placement, null-last-beat handling, errored-frame and overflow drops,
  in-order survival, TX "tvalid never drops mid-packet".
- ✓ `ilock_pl` elaborates with both interlock cores.
- ✗ Nothing has been synthesized for the VP1802 (no device support/license on
  this box). The Vivado scripts are written against the MRMAC v3.2 catalog
  definition but have not been executed.
- ✗ Which `tdata<N>` words a port uses, the example design's exact module
  names, and the CIPS/GT automation details are to be confirmed on the Versal
  machine.
- ✗ The XPM FIFO instantiations compile in Vivado's XPM library only; the
  benches use the behavioural stand-in.

## 8. Open items

- Link parameters (optics, peer, rate, FEC, cages) — §4.
- Whether the peers can run a 100GbE (or 25GbE) mode that the optics support;
  a DGX Spark's ConnectX-7 QSFP112 ports do 200G natively and 25/50/100G lane
  modes, but NVIDIA lists port splitting as unvalidated on the Spark.
- Access to the Versal machine (hostname, Vivado version, license) to run the
  flow; the scripts assume Vivado 2025.x and MRMAC v3.2.
- Later: raise `core_clk` and widen the core datapath if line-rate
  attestation is ever needed.
