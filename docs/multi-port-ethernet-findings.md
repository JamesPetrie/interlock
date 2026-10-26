# Multi-Port Ethernet Bring-Up: Findings and References

```
TODO (James): Claude reviewed this doc (2026-09-18) and thinks it should be
updated now that we have a working version.
```

This document collects what we've learned during the multi-port (CORETSE_0
+ CORETSE_1) Ethernet bring-up on the MPF300-EVAL-KIT. Captured because
much of this is hard to assemble from any single source — the relevant
information is spread across encrypted IP wrappers, half-documented
register pages, and forum threads.

## System overview

```
                                  MPF300 PolarFire FPGA
   ┌────────────────────────────────────────────────────────────────┐
   │                                                                │
Mac│   ┌──────────┐                                                 │ Spark
───┼───┤ VSC8575  │  RJ45 J15 ─ port 0 ─── XCVR lane 0              │ (J30 ─ port 1)
   │   │ quad PHY │                                                 │
   │   │ ports    │  RJ45 J30 ─ port 1 ─── XCVR lane 1              │
   │   │ 0..3     │                                                 │
   │   └──┬───────┘                                                 │
   │      │ MDIO bus (shared MDC + MDIO, single master)             │
   │      │                                                         │
   │      ├──── PHY 28 ─ port 0 copper PHY (MII regs)               │
   │      ├──── PHY 29 ─ port 1 copper PHY (MII regs)               │
   │      ├──── PHY 30 ─ port 2 copper PHY (unused)                 │
   │      ├──── PHY 31 ─ port 3 copper PHY (unused)                 │
   │      ├──── PHY 18 ─ CORETSE_0's internal MDIO slave (PCS)      │
   │      └──── PHY 19 ─ CORETSE_1's internal MDIO slave (PCS)      │
   │                       └────────────────────────────────────────┤
   │     (CORETSE_1's slave SILENT — root cause under investigation)│
   │                                                                │
   └────────────────────────────────────────────────────────────────┘
```

## Authoritative documents

### CoreTSE soft MAC IP

- **CoreTSE v3.2 User Guide DS50003245C** — IP-level documentation.
  PDF: <https://ww1.microchip.com/downloads/aemDocuments/documents/FPGA/ProductDocuments/UserGuides/ip_cores/directcores/CoreTSE_UG.pdf>
  - §1.10 MDIO Management — describes the IP's MDIO master + internal slave
  - §3.1 MAC Core Registers — register map (TSE_BASEADDR + 0x000-0x044)
  - §3.5 SGMII/TBI/1000Base-X Registers (Indirect Addressing through MDIO)
    — registers accessible at the internal slave address (MDIO_PHYID)
  - §4.1 Ports — pin descriptions (notably MDC = output only, no MDC input)
  - §4.2 Configuration Parameters — defines `MDIO_PHYID` (range 0-31,
    default 18) as "MDIO Physical Address"

- **CoreTSE Handbook DS50003245E** — newer handbook covering the same IP.
  PDF: <https://ww1.microchip.com/downloads/aemDocuments/documents/FPGA/ProductDocuments/UserGuides/ip_cores/directcores/CoreTSE_HB.pdf>
  Confirms MDI/MDO/MDOEN are all in the PCLK clock domain (no external
  MDC input on the core).

- **UG0687 PolarFire 1G Ethernet Solutions User Guide**.
  PDF: <https://ww1.microchip.com/downloads/aemDocuments/documents/FPGA/ProductDocuments/UserGuides/microsemi_polarfire_fpga_1g_ethernet_solutions_user_guide_ug0687_v5.pdf>
  Background on Ethernet IP options for PolarFire. Defines MDC/MDO/MDOEN/MDI
  port semantics. Silent on multi-MAC use.

### VSC8575 PHY chip

- **VMDS-10457 (VSC8575-11 datasheet)** Rev 4.2 — the authoritative source
  on the PHY chip side.
  Product page (with datasheet link): <https://www.microchip.com/en-us/product/vsc8575-11>
  - **§3.1 Operating Modes** — Table 1 lists supported MAC↔Media mode
    combinations. SGMII MAC ↔ 10/100/1000BASE-T copper is what we use.
  - **§3.1.1.1 MAC Interface SGMII** — explicit register-level setup
    required for SGMII MAC mode:
    > "• Set register 19G bits 15:14 = 00
    >  • Set Register 23 (main register) bit 12 = 0
    >  • Set Register 18G = 0x80F0"
  - **§3.2 SerDes MAC Interface** — clarifies the global vs per-port nature:
    > "Register 19G is a global register and needs to be set once to
    >  configure the device to the desired mode. The other register bits
    >  are configured on a per-port basis and the operation either needs
    >  to be repeated for each port, or a broadcast write needs to be
    >  used by setting register 22, bit 0 to configure all the ports
    >  simultaneously."
  - **§4.2.28 (Table 68) Extended/GPIO Register Page Access** —
    authoritative table for register-31 page values:

    | Value | Page |
    |---|---|
    | 0x0000 | Main (default) |
    | 0x0001 | Extended page 1 (E1) |
    | 0x0002 | Extended page 2 (E2) |
    | 0x0003 | Extended page 3 (E3) |
    | 0x0010 | GPIO page (= "G" suffix) |

  - **§4.7 General Purpose Registers** — describes the GPIO/G page register
    map. Notable registers:
    - 18G (Table 118) "Microprocessor Command Register":
      - 0x80F0 = "Enable four MAC SGMII ports"
      - 0x80E0 = "Enable four MAC QSGMII ports"
      - 0x8FC1 = "Enable four Media 1000BASE-X ports"
      - 0x8FD1 = "Enable four Media 100BASE-FX ports"
    - 19G (Table 119) "MAC Mode and Fast Link Configuration":
      - bits 15:14 = 00 → SGMII, 01 → QSGMII, others reserved
  - **§4.7.7 Microprocessor Command** — operational notes:
    > "Bit 15 tells the internal processor to execute the command. When
    >  bit 15 is cleared the command has completed. ... Bit 14 = 1
    >  typically indicates an error condition where the squelch patch
    >  was not loaded. ... Commands may take up to 25 ms to complete."
  - **§4.2.21 (Table 61) Extended PHY Control 1 (register 23)** — per-port
    register; bit 12 selects SGMII (0) vs 1000BASE-X (1) at the per-port
    level. Note: "Register 19G.15:14 must be = 00 for this selection to
    be valid." This bit is **super-sticky** (survives software reset);
    a software reset (register 0 bit 15) is needed to commit mode changes.

### Linux kernel mscc driver

Production driver for the VSC85xx PHY family. Authoritative
cross-reference for any VSC PHY register-access details.

- mscc.h (constants): <https://raw.githubusercontent.com/torvalds/linux/master/drivers/net/phy/mscc/mscc.h>
- mscc_main.c (main driver): <https://raw.githubusercontent.com/torvalds/linux/master/drivers/net/phy/mscc/mscc_main.c>
- Driver source tree: <https://github.com/torvalds/linux/tree/master/drivers/net/phy/mscc>

`drivers/net/phy/mscc/mscc.h` confirms the page values:

```c
#define MSCC_EXT_PAGE_ACCESS              31     // the page-select register
#define MSCC_PHY_PAGE_STANDARD            0x0000
#define MSCC_PHY_PAGE_EXTENDED            0x0001
#define MSCC_PHY_PAGE_EXTENDED_2          0x0002
#define MSCC_PHY_PAGE_EXTENDED_3          0x0003
#define MSCC_PHY_PAGE_EXTENDED_4          0x0004
#define MSCC_PHY_PAGE_EXTENDED_GPIO       0x0010   // "G" suffix lives here
#define MSCC_PHY_PAGE_1588                0x1588
```

And confirms that the SGMII/QSGMII MAC interface mode is selected via
register 19 on the GPIO page (= 19G):

```c
#define MSCC_PHY_MAC_CFG_FASTLINK         19      // register address on GPIO page
#define MAC_CFG_MASK                      0xc000  // bits 15:14
#define MAC_CFG_SGMII                     0x0000  // matches VSC8575 datasheet
#define MAC_CFG_QSGMII                    0x4000
#define MAC_CFG_RGMII                     0x8000
```

This is the same as the datasheet's 19G[15:14] description but lets us
cross-check the page mapping definitively.

### Microchip demo guides (multi-instance hints)

- **DG0633 IGLOO2 CoreTSE MAC 1000 Base-T Loopback Demo** (single-port
  demo) — contains the only explicit Microchip statement we've found on
  multi-instance CoreTSE:
  > "Multiple CoreTSE MAC IPs can be used in IGLOO2 to achieve Ethernet
  >  solutions. CoreTSE MAC can be used in SmartFusion2 devices along
  >  with MSS Ethernet MAC to support multiple Ethernet interfaces."

  PDF: <https://ww1.microchip.com/downloads/aemdocuments/documents/fpga/ProductDocuments/UserGuides/m2gl_dg0633_core_tse_mac_1000_baset_loopback_v1.pdf>

  But DG0633 itself is single-instance and doesn't show how. It punts to
  AC423 for details.

- **DG0637 SmartFusion2 CoreTSE_AHB 1000 Base-T Loopback Demo** — sister
  demo for SmartFusion2. Echoes the DG0633 multi-instance language
  verbatim. Same situation: single-port demo, no example.
  PDF: <https://ww1.microchip.com/downloads/aemDocuments/documents/FPGA/ApplicationNotes/ApplicationNotes/microsemi_smartfusion2_coretse_ahb_1000_baset_loopback_demo_guide_dg0637.pdf>

- **AC423 SmartFusion2/IGLOO2 Ethernet Solutions Application Note** — the
  document Microchip explicitly cites for multi-MAC details. **Retrieved
  via archive.org** (Microchip CDN returns HTTP 403 to non-browser fetches):
  <https://web.archive.org/web/2024/https://www.microsemi.com/document-portal/doc_download/134017-ac423-smartfusion2-soc-fpga-and-igloo2-fpga-ethernet-solutions-application-note>

  **VERDICT: AC423 does NOT document a multi-CoreTSE pattern.** It
  contains only one multi-MAC note (§2.3 line 297):

  > "Note: If the application requires two MACs, Core10100 MAC can be
  >  used in conjunction with the SmartFusion2 embedded MSS MAC to
  >  provide the required functionality."

  So Microchip's documented multi-MAC pattern is **one HARD MAC + one
  SOFT MAC of a different type** (MSS MAC + Core10100), not two of the
  same soft MAC. AC423's figures show "MSS MAC OR CoreTSE" — singular,
  never both. The DG0633 claim that "Multiple CoreTSE MAC IPs can be
  used in IGLOO2" is **aspirational and unsupported by any concrete
  example or AC423 itself**. Our dual-CoreTSE-on-shared-MDIO approach
  is not in Microchip's documented design space.

  AC423 also says nothing about PolarFire (it's SmartFusion2/IGLOO2
  only) or VSC8575 register-level init.

- **AN4623** — 1G Ethernet for PolarFire reference application note. Our
  firmware is derived from its single-port reference design.
  Product page (with download link): <https://www.microchip.com/en-us/application-notes/an4623>
  - **Appendix 3 "Multi-Lane 1G IOD CDR Design"** (DS00004623A page 29-30)
    documents multi-lane PolarFire designs using IOD CDR. Shows an 8-lane
    design with 8 PF_IOD_CDR instances sharing 1 PF_IOD_CDR_CCC. **Confirms
    that our two-CDR + shared-CCC gateware topology is the documented
    pattern** — the gateware-internal multi-lane substrate is not exotic.
    However, this appendix is silent on multi-MAC/MDIO/VSC8575 config.
  - **Appendix 4 "1G Ethernet BASE-T and BASE-X Using Transceiver"**
    (DS00004623A page 31+) describes an alternative architecture using
    PolarFire's hard transceiver instead of IOD CDR. Not what we use, but
    references HB0549 (CoreTSE v3.1 Handbook) and HB0627 (CoreSGMII v3.2
    Handbook) for auto-negotiation register details. Notes that the MDIO
    slave (PHY 18 / PHY 19 in our addressing) holds the Clause 37
    auto-negotiation state machine for the MAC↔PHY SGMII link — so when
    PHY 19 is silent, port 1's Clause 37 negotiation can't run.

- **DG0799 PolarFire 1G Ethernet IOD CDR Demo** — PolarFire-specific
  single-SGMII ethernet demo. Useful reference for PF_IOD_CDR usage.
  Product page: <https://www.microchip.com/en-us/application-notes/dg0799>

### Microchip support articles (relevant but blocked)

Both returned HTTP 403 to automated fetches; worth retrieving via a
browser session if we keep debugging:

- **MDIO interface on the CoreSGMII**:
  <https://microchip.my.site.com/s/article/MDIO-interface-on-the-CoreSGMII>
- **Connecting multiple PHYs to one MDIO bus**:
  <https://microchip.my.site.com/s/article/Connecting-multiple-PHYs-to-one-MDIO--Management-Data-Input-Output--bus>

### Other relevant findings

- **`polarfire-soc-video-kit-reference-design`** on GitHub — closest
  multi-link PolarFire example we found. It uses **CoreSGMII + MSS GEM**
  with **two separate MDIO buses** (one per MAC), not two CoreTSE soft
  IPs on a shared bus. Microchip's published reference pattern when they
  want two MACs talking to PHYs is **bus separation**, not sharing.
  - Repo: <https://github.com/polarfire-soc/polarfire-soc-video-kit-reference-design>
  - TSN top-level wiring (two separate MDIO buses):
    <https://github.com/polarfire-soc/polarfire-soc-video-kit-reference-design/blob/master/script_support/components/TSN/VKPFSOC_TSN.tcl>
  - CoreSGMII instantiation with `MDIO_PHYID:18`:
    <https://github.com/polarfire-soc/polarfire-soc-video-kit-reference-design/blob/master/script_support/components/TSN/CORESGMII_C0.tcl>

- **`polarfire-soc` GitHub Discussion #211** ("Using both Ethernet ports on
  Icicle kit") notes the Icicle kit's two MSS GEMs share a single MDIO
  bus to the dual-port PHY, and "you can use them one at a time, but
  not both at the same time" — a precedent that even Microchip's hardware
  designs back away from concurrent multi-MAC + shared MDIO.
  URL: <https://github.com/orgs/polarfire-soc/discussions/211>

### Open-source MAC alternatives (if we abandon dual-CoreTSE)

- **alexforencich/verilog-ethernet** — production-quality 1G/10G Ethernet
  MAC + UDP/IP stack in pure Verilog, BSD-licensed, widely used (Corundum
  NIC, others). Would replace CORETSE_1 if we pivot to CoreSGMII + open MAC.
  Repo: <https://github.com/alexforencich/verilog-ethernet>

- **secworks/sha256** — well-tested SHA-256 core for future attestation
  work (BSD).
  Repo: <https://github.com/secworks/sha256>

- **secworks/hmac** — HMAC wrapper around the SHA-256 core (BSD).
  Repo: <https://github.com/secworks/hmac>

## Key facts established empirically

### What we have confirmed works

- **CORETSE_0 alone, single-port**: works completely. PHY 18 (its internal
  PCS slave) responds with `status = 0x017D` after autoneg, link bit
  set, autoneg-done bit set.

- **VSC8575 ports 28/29/30/31 all respond on MDIO**: every PHY 28-31 returns
  valid PHY ID (`0x0007 / 0x07D1` = the OUI for Vitesse/Microsemi).

- **VSC8575 port 0 and port 1 copper links** both come up. `status = 0x79ED`
  on both, link=1, autoneg complete=1.

- **CORETSE_1's MAC layer works**: MAC_CONFIG_1 reads back as 0x0F after
  init (= TXEN+SYNCED_TXEN+RXEN+SYNCED_RXEN), proving APB CSR access and
  MAC sync are functional.

### What we have NOT been able to make work

- **CORETSE_1's internal MDIO slave at PHY 19** returns `0xFFFF` on every
  read. Symptom unchanged across:
  - Different SmartDesign configurations (single component instantiated
    twice; two separate components with different MDIO_PHYID values; wrapper
    Verilog parameterization attempts)
  - Connecting MDI to the shared bus (vs leaving it tied to GND)
  - Adding `mdio_combiner` to route CORETSE_1's MDO/MDOEN back onto the bus
  - Six bitstream rebuilds with various combinations of the above

## What we've ruled out

| Hypothesis | Status | Evidence |
|---|---|---|
| MDIO_PHYID parameter not propagating | **Ruled out**. Synth log shows two distinct parameter-specialized variants `ACT_UNIQUE_CORETSE_TOP_..._18s_0s` and `..._19s_0s`. |
| Synthesizer collapsing instances | **Ruled out**. Same as above — Synplify keeps two distinct instances with different parameter values. |
| Bus contention from second master | **Ruled out**. CORETSE_1's master is unused (MDC tied off, MDO/MDOEN routed only as slave response path). |
| Bridge wiring asymmetry between ports | **Ruled out**. Verified all clocks, resets, MDI connections symmetric between CORETSE_0 and CORETSE_1. |
| CORETSE_1's slave can't drive bus (no MDO/MDOEN) | **Tested (sub-PR #9)** — added mdio_combiner so slave response can reach BIBUF. No change. |

## Still open (current best hypotheses, iter-3 testing now)

### Hypothesis A — VSC8575 missing package-level SGMII init

The VSC8575 datasheet explicitly requires writes to 19G (global) and 18G
(microprocessor command 0x80F0) to put the chip in 4-port SGMII MAC mode.
Until iter-3, we never made these writes. Port 0 might work because the
chip's reset defaults are SGMII-ish for port 0, but ports 1-3 might not
be fully active without the explicit init.

If true: PHY 19 should now respond after iter-3.

### Hypothesis B — Squelch patch not loaded

The VSC8575 has an 8051 microcontroller running ROM firmware. Microchip
provides "patches" loaded over MDIO that fix ROM bugs. The Linux mscc
driver loads these patches (`vsc8584_micro_assert_reset` etc.); we don't.

Per §4.7.7 of the datasheet, the 18G microprocessor command sets bit 14
on completion if "the squelch patch was not loaded." Iter-3 explicitly
checks this bit and logs it.

If bit 14 = 1: we need to load the firmware patch, which is a ~10 KB
binary blob loaded over MDIO into the chip's RAM. Significant additional
firmware work.

### Hypothesis C — CoreTSE eval-tier multi-instance limitation

The IP is IEEE 1735 encrypted. The CoreTSE User Guide is silent on
multi-instance use. DG0633 says multi-instance "can be used" but provides
no example. The encrypted RTL might have implicit single-instance
assumptions even though MDIO_PHYID is correctly parameterized at the
synth level.

If true: iter-3's spec-compliant VSC8575 init is irrelevant; PHY 19 stays
silent because the CoreTSE_1 PCS subsystem doesn't fully activate.

### Hypothesis D — Slave needs explicit MDC

The CoreTSE MDIO slave might be implemented to sample MDI on its own
master's MDC. If CORETSE_1's master is idle (no firmware APB writes),
its internal MDC isn't toggling and the slave can't decode bus traffic
from CORETSE_0's master.

We considered this earlier but the handbook's port-domain listing
(MDI/MDO/MDOEN all in PCLK clock domain, no MDC input) implies PCLK
oversampling rather than MDC sampling. Plausible but probably not the
issue.

## Iteration log

| Iter | Approach | Result |
|---|---|---|
| #1 | AN4623 single-port baseline | Port 0 works |
| #3 | Sub-PR #3: second CoreTSE instance with internal loopback | Built, port 1 isolated |
| #4 | Sub-PR #4: bidirectional bridge wiring | Built, no functional test yet |
| #5 | Sub-PR #5: debug LEDs | Built, port 0 works, port 1 silent |
| iter-2 fw | Port-1 firmware mirroring port 0 | Built, PHY 19 = 0xFFFF |
| #8 | Sub-PR #8: distinct CoreTSE component for CORETSE_1, MDIO_PHYID=19 | Built (with Synplify `-allow_duplicate_modules`), PHY 19 still 0xFFFF |
| #9 | Sub-PR #9: mdio_combiner module | Built, PHY 19 still 0xFFFF |
| iter-3 fw | + VSC8575 4-port SGMII config + diagnostic probes | VSC8575 confirmed OK; PHY 19 electrically identical to unused PHY 17 |
| **iter-4** | **Two-bus MDIO architecture (tse1_loopback)** | **✅ WORKS. PHY 19 responds via TSE1_BASEADDR with same pattern as PHY 18. Mac→Spark ping ~1.5 ms via FPGA bridge.** |

## ✅ RESOLUTION

The dual-CoreTSE bridge works with the **two-bus MDIO architecture**:

- **External MDIO bus** carries only CORETSE_0's transactions (to VSC8575 PHY
  chip + CORETSE_0's own internal slave at MDIO addr 18).
- **Internal MDIO bus** (fabric-only loopback via `tse1_loopback.sv`) carries
  only CORETSE_1's transactions to its own internal slave at MDIO addr 19.
- Firmware accesses PHY 19 via `TSE1_BASEADDR` (CORETSE_1's APB MDIO master)
  so CORETSE_1's internal MDC is actively toggling, which is the precondition
  for the slave to function.

The undocumented finding is:

> **CoreTSE's internal MDIO slave requires its own master to be active
>  (generating MDC) for the slave to function.** A second CoreTSE instance
>  whose master is idle, sharing an external MDIO bus, will have a silent
>  slave — the slave's logic appears to be clocked off the internal MDC
>  generated by its own master, not by external bus traffic.

This isn't in any Microchip document we found (CoreTSE UG, Handbook, DG0633,
AC423, DG0799). DG0633 says "multiple CoreTSE MAC IPs can be used" but
provides no example; AC423 (cited by DG0633) doesn't either. The two-bus
workaround is what we discovered empirically with iter-3's diagnostic probes
followed by the iter-4 architectural fix.

## What to look at if iter-3 doesn't fix it

The diagnostic prints in iter-3 will narrow the failure mode. Specifically:

- `[vsc] 18G post-cmd=0x????` — if bit 14 set, squelch patch needed
- `[pcs] PHY19 ...` indicator bits — if i=00 (clean read of 0xFFFF), slave
  isn't engaging; if i=04 (NOT_VALID), master is timing out
- Compare PHY 19 indicator bits to PHY 17 (unused negative control) — if
  identical, PHY 19 is "fully silent like nothing-at-all"

If we're stuck after iter-3, the remaining strategic options:

1. **Load the VSC8575 firmware patch** — significant work but well-documented
   in the Linux mscc driver.

2. **Pivot to CoreSGMII + alexforencich MAC for port 1** — replaces the
   encrypted CoreTSE-multi-instance unknown with a known-good open-source
   stack. ~1-2 weeks.

3. **Passive observer architecture** — single CORETSE for Mac side, Spark
   connects directly to Mac, FPGA observes via fabric tap. Eliminates
   second-port problem entirely. Most aligned with interlock's actual
   threat model.

4. **Retrieve AC423** — the document Microchip explicitly cites for
   multi-instance CoreTSE configuration. Couldn't fetch automatically;
   needs a browser session.

## File-by-file reference

### Gateware (Libero / SmartDesign Tcl)

- `gateware/src/src_components/CORETSE_0.tcl` — primary CoreTSE component,
  MDIO_PHYID=18 (default)
- `gateware/src/src_components/CORETSE_1.tcl` — second CoreTSE component,
  MDIO_PHYID=19 (NEW in sub-PR #8)
- `gateware/src/src_components/top.tcl` — SmartDesign top wiring
- `gateware/src/src_hdl/mdio_combiner.sv` — wire-OR for two CoreTSE slaves
  to share one MDIO bus (NEW in sub-PR #9)
- `gateware/src/4_implement_design.tcl` — synthesis config; carries
  `SYNPLIFY_OPTIONS:set_option -allow_duplicate_modules 1` (NEW in sub-PR
  #8) so Synplify accepts two CoreTSE.v files with identical encrypted
  sub-module names

### Firmware

- `gateware/src/src_softconsole/main.c` — Mi-V firmware. Iter-3 adds
  `vsc8575_init()`, `mdio_read_v()`, `dump_pcs()`.
- `gateware/src/src_softconsole/iog_cdr.hex` — baked into bitstream at
  build time; must be regenerated whenever main.c changes (SoftConsole
  build then objcopy with `--change-section-lma '*-0x80000000'`).

### Documentation

- `docs/firmware-iteration.md` — fast iteration via JTAG (currently not
  fully working — but useful reference for SoftConsole setup)
- This file (`docs/multi-port-ethernet-findings.md`) — what you're
  reading.
