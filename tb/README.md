# tb/ — cocotb testbenches

Unit testbenches for fabric-only SystemVerilog modules. Run in seconds via
[cocotb](https://www.cocotb.org) + [Icarus Verilog](http://iverilog.icarus.com)
on the Hetzner box. No FPGA toolchain needed — these run on any Linux box
with the venv installed.

## Quick start

```
# From repo root
make test-fabric-bridge   # run fabric_bridge tests
make test                 # alias for all tests
make clean-test           # wipe sim_build dirs
```

Tests run under `python3` from `.venv/`. Created automatically by `make`'s
`.venv` target if missing.

## Layout

```
tb/
├── common.mk                    # shared cocotb / simulator settings
├── README.md                    # this file
└── test_<module>/
    ├── Makefile                 # per-test Makefile (sets TOPLEVEL, sources)
    └── test_<module>.py         # cocotb test functions
```

One sub-directory per top-level module under test. Each Makefile is ~10 lines.

## Adding a new test

1. Pick a module name (e.g., `pkt_counter`).
2. Create `tb/test_pkt_counter/Makefile` cribbed from `test_fabric_bridge/Makefile`
   — set `TOPLEVEL`, `MODULE`, `VERILOG_SOURCES`.
3. Write `tb/test_pkt_counter/test_pkt_counter.py` with `@cocotb.test()` functions.
4. Add a `test-<name>` target in the repo root Makefile.

## Simulator choice

Default is **Icarus Verilog** (`SIM=icarus`). Reliable, easy to install
(`apt install iverilog`), works with cocotb 2.x out of the box.

Verilator support is in `common.mk` (`SIM=verilator`) but Ubuntu 24.04's
apt verilator (5.020) is older than cocotb 2.x needs (≥ 5.036). If you want
verilator's speedup for large simulations, build it from source. For the
testbenches we have today, icarus is fast enough (~0.4s for all
`fabric_bridge` tests).

## What's worth testing here vs. in hardware

cocotb tests are appropriate for:
- New fabric-only SystemVerilog modules (no vendor IP)
- Modules where the logic is non-trivial enough to benefit from sub-second
  iteration (FIFOs with CDC, FSMs, hash chains, etc.)
- Properties that are hard to debug on hardware (intermittent CDC issues,
  drop semantics under load)

Hardware testing remains the gate for:
- Pin assignments, bank attributes, polarity
- Vendor IP (CoreTSE, PF_IOD_CDR, PF_CCC) behavior
- PHY interaction, SGMII handshake, link bring-up
- End-to-end integration with the Mac / Spark over real ethernet
