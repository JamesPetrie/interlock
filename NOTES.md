# AN4623 build reproduction notes

The shipped `mpf_an4623_v2022p3_df` bundle was written against Libero
v2022.3. Four edits make it build cleanly under v2024.2. They're already
applied in `gateware/`; this file documents them so future-you (or a
future agent) can understand the deltas vs. upstream and re-apply them
against a newer Microchip release if needed.

## Source

```
curl -L -A "Mozilla/5.0" \
     -e "https://www.microchip.com/en-us/application-notes/AN4623" \
     -o mpf_an4623_v2022p3_df.zip \
     "https://ww1.microchip.com/downloads/aemDocuments/documents/FPGA/SOCDesignFiles/mpf_an4623_v2022p3_df.zip"
```

13.2 MB, no login required. AN4623 == DG0799 (Microchip renamed it).
Bare `curl` to `ww1.microchip.com` returns 403 without the browser-like
User-Agent and Referer.

## Edits applied vs. upstream

### 1. `gateware/common.tcl`

Two IP version bumps:

```diff
-set PF_INIT_MONITORver {2.0.304}
+set PF_INIT_MONITORver {2.0.307}
-set PF_IOD_CDRver {2.4.105}
+set PF_IOD_CDRver {2.4.110}
```

Both pinned versions have broken packages in v2024.2's catalog —
`download_core` "succeeds" but only extracts `core_xml.zip`, not the TCL
filesets, so `create_and_configure_core` then fails with "Cannot find
Spirit core configuration file." The next-newer versions are fine.

### 2. `gateware/src/src_components/pf_init_monitor_0.tcl` (line 5)

The version is also hardcoded in the per-component Tcl:

```diff
-create_and_configure_core -core_vlnv {Actel:SgCore:PF_INIT_MONITOR:2.0.304} ...
+create_and_configure_core -core_vlnv {Actel:SgCore:PF_INIT_MONITOR:2.0.307} ...
```

### 3. `gateware/src/src_components/PF_IOD_CDR_C0.tcl` (line 5)

Same pattern for PF_IOD_CDR:

```diff
-create_and_configure_core -core_vlnv {Actel:SystemBuilder:PF_IOD_CDR:2.4.105} ...
+create_and_configure_core -core_vlnv {Actel:SystemBuilder:PF_IOD_CDR:2.4.110} ...
```

### 4. `gateware/script.tcl` — pre-download block

Inserted after `puts "Project created successfully"`. Mirrors the pattern
Microchip's own SmartHLS-2024.2 PolarFire scripts use
(`/usr/local/microchip/Libero_SoC_v2024.2/SmartHLS-2024.2/SmartHLS/smarthls-library/external/vision/rtl/`).
Pre-downloads each pinned IP before `create_and_configure_core` is
called:

```tcl
set _direct_repo {www.microchip-ip.com/repositories/DirectCore}
set _sg_repo     {www.microchip-ip.com/repositories/SgCore}
foreach _entry [list \
        "Actel:DirectCore:CoreAPB3:$CoreAPB3ver $_direct_repo" \
        "Actel:DirectCore:COREJTAGDEBUG:$COREJTAGDEBUGver $_direct_repo" \
        "Actel:DirectCore:CORESPI:$CORESPIver $_direct_repo" \
        "Actel:DirectCore:CORETSE:$CORETSEver $_direct_repo" \
        "Actel:DirectCore:CoreUARTapb:$CoreUARTapbver $_direct_repo" \
        "Actel:DirectCore:CORERESET_PF:$CORERESET_PFver $_direct_repo" \
        "Microsemi:MiV:MIV_RV32:$MIV_RV32ver $_direct_repo" \
        "Actel:SgCore:PF_CCC:$PF_CCCver $_sg_repo" \
        "Actel:SgCore:PF_INIT_MONITOR:$PF_INIT_MONITORver $_sg_repo" \
        "Actel:SystemBuilder:PF_IOD_CDR:$PF_IOD_CDRver $_sg_repo" \
        "Actel:SystemBuilder:PF_IOD_CDR_CCC:$PF_IOD_CDR_CCCver $_sg_repo"] {
    set _vlnv [lindex $_entry 0]
    set _loc  [lindex $_entry 1]
    puts "Pre-downloading $_vlnv ..."
    if {[catch {download_core -vlnv $_vlnv -location $_loc} _err]} {
        puts "  WARNING: $_err"
    }
}
```

## Expected build output

- Wall clock: **~22 min** (SYNTHESIZE 2.5 min, PLACEROUTE ~10 min, rest <2 min)
- Peak swap: **~1 GB** (well within the 8 GB swapfile on the Hetzner box)
- `.job` at `gateware/Libero_Project/designer/top/export/top.job`, ~12.1 MB,
  software version `2024.2.0.13`
- Resource: ~6% of MPF300 (~18.5K 4-LUTs, ~8.1K DFFs)
- Reference entire-bitstream digest from one verified build:
  `093b9ea7780eaa15254d0490ae6d9c13d70b50811c6006968b4b7f55835faaac`
  (yours will differ because of embedded timestamps — that's expected;
  the digest is for sanity-checking that you're in the same ballpark)

## Recovery if the vault gets confused

If you see "Cannot find Spirit core configuration file" even with the
patches above applied, the vault has stale state from a previous failed
download attempt. Wipe and let `download_core` re-fetch:

```bash
rm -rf /root/.actel/vault/Components/Actel/SystemBuilder/PF_IOD_CDR
rm -rf /root/.actel/vault/Components/Actel/SystemBuilder/PF_IOD_CDR_CCC
python3 -c "
import re
t = open('/root/.actel/vault/index.xml').read()
for n, v in [('PF_IOD_CDR','2.4.110'), ('PF_IOD_CDR_CCC','2.1.111')]:
    p = rf'<core_mlv [^>]*>\s*<vendor>Actel</vendor>\s*<library>SystemBuilder</library>\s*<name>{n}</name>\s*<version>{re.escape(v)}</version>.*?</core_mlv>'
    t = re.sub(p, '', t, flags=re.DOTALL)
open('/root/.actel/vault/index.xml','w').write(t)
"
./build.sh
```

## PF_INIT_MONITOR on Libero >= 2025.1

The 2.0.307 package the kit was validated with downloads under Libero 2025.x
but extracts only its `core_xml` fileset (3 files instead of 9; a clean
wipe-and-refetch gives the same), so `create_and_configure_core` fails with the
Spirit error above. 2.0.308 (`min_libero_version` 2025.1) is complete.
`common.tcl` therefore keeps 2.0.307 as the kit's pin and switches to 2.0.308
when `get_libero_release` reports 2025.1 or newer; nothing to edit. `PF_CCC`
2.2.220 configures fine on 2025.x (it is also what `PF_IOD_CDR_CCC` 2.1.111
depends on). A 2025.x install keeps its vault at
`/usr/local/microchip/common/vault`, not `~/.actel/vault` as the recipe above
assumes.
