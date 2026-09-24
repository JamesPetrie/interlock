# gateware_vpk180 — Versal Premium (VPK180) port of the interlock

The interlock core RTL in `../gateware/src/src_hdl` is unchanged; this
directory adds what replaces the PolarFire-specific parts (CoreTSE, IOD CDR,
Mi-V firmware, VSC8575/MDIO) on a VPK180 whose QSFP-DD cages carry optics:

```
hdl/          RTL: MRMAC pin adapter, per-port shim (drop FIFO, CDC, width
              conversion, CoreTSE-bundle adapters), PL top ilock_pl
params.tcl    LINK PARAMETERS — optics / rate / FEC / cages. Fill in first.
scripts/      Vivado flow: gen_exdes -> integrate -> build -> program
constraints/  LEDs + QSFP-DD sideband placeholders
build/        (gitignored) Vivado projects and outputs
```

Design notes, board facts, the parameter checklist and the bring-up plan are
in [`../docs/vpk180-port.md`](../docs/vpk180-port.md).

## Simulation (any Linux box)

```
make test-vpk180            # cocotb + Icarus, all shim benches, W=64
make test-vpk180 W=384      # 100GbE interface width
make lint-vpk180            # Verilator -Wall on ilock_pl with both cores
```

## Synthesis (Versal machine)

```
vi params.tcl               # rate, preset, FEC, quads, MAC_AXIS_W
./build.sh exdes            # MRMAC example design for that config
./build.sh integrate        # adds the interlock RTL; then edit the exdes top
./build.sh build            # -> build/out/*.pdi
./build.sh program          # JTAG
```
