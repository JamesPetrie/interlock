# interlock

Hardware prototype of the verifier interlock described in the *Inference
Verification Prototype* design doc — a small FPGA that sits between AI
compute and the outside world, attesting to canonical packet streams via
HMAC-chained hashes.

This repo starts from Microchip's AN4623 (PolarFire 1G Ethernet Loopback
Using IOD CDR) demo as a working baseline. Modifications layer on top: a
packet counter and UART status print first, then port-to-port bridging,
then the actual interlock data path (hash chain, drop rules, HMAC
attestation via the PolarFire System Controller).

## Status

*(refreshed 2026-08-13)*

| Milestone | Status |
|---|---|
| AN4623 baseline: builds on Libero v2024.2, flashes, loopback verified | ✓ |
| Bidirectional port-to-port bridge | ✓ |
| Frame sanitization + canonical packet processing (drop rules) on silicon | ✓ 2026-06-16 |
| Per-packet hash commits + HMAC-authenticated traffic certificates on silicon | ✓ 2026-06-16 |
| DGX Spark bench: flash chain (x86 VM) + two test NICs | ✓ (`docs/flashing-and-testing.md`) |
| Multi-turn inference CLI demo over the interlock, in-band ZK challenge | ✓ 2026-06-18 (branch `docs/inference-cli-app`) |
| Recomp interlock core (recompute-feed path) — what `main` builds today | ✓ on silicon 2026-07-20 |
| Prod top at 1 ms buckets (bidirectional, ~6e-6 drop rate at ~97 Mb/s each way) | ✓ on silicon 2026-07-20 (branch `build/prod-1ms`) |
| Bucket-timing / throughput characterization on the bench | ✓ 2026-08-11 |
| Build-config knobs (`TOP={recomp,prod}`, `BUCKET_MS={100,1}`) merged to main | — (`feature/build-config-knobs`) |

> **What `./build.sh` on `main` builds today:** the **recomp** top with a
> **100 ms testing-override bucket period**. Sync + cert traffic appears on
> port 0 only and only the 0→1 direction is processed — a silent port 1 is
> expected on this build, not a setup problem. The production bidirectional
> top at 1 ms is branch `build/prod-1ms`. Details in `docs/SETUP.md` §9
> ("Which design did I just build?").

**New to the project?** Read in order:
[`docs/interlock-overview.md`](docs/interlock-overview.md) (what the device
is and its security properties) → this README →
[`docs/SETUP.md`](docs/SETUP.md) (build environment) →
[`docs/flashing-and-testing.md`](docs/flashing-and-testing.md) (flash + test
on a bench) → [`bench/README.md`](bench/README.md) (the runnable scripts).
Setting up the Raspberry Pi side of the demo:
[`docs/pi-setup.md`](docs/pi-setup.md).

## Hardware target

- **Board:** Microchip MPF300-EVAL-KIT (Rev D), MPF300TS-1FCG1152I, Silver license tier
- **Tools:** Libero SoC v2024.2 on Linux, FlashPro Express v2024.2.0.13
- **External:** Mac (sender / test harness) and NVIDIA DGX Spark (eventual prover compute) wired into the board's two RJ45 ports

## Layout

```
interlock/
├── gateware/             # Microchip Tcl + RTL — patched AN4623 source for now
│   ├── common.tcl        # IP versions pinned at v2024.2-compatible values
│   ├── script.tcl        # Top-level: new_project → download cores → source flow scripts
│   └── src/
│       ├── 1_create_design.tcl    # SmartDesign assembly
│       ├── 2_constrain_design.tcl # I/O & timing constraints
│       ├── 4_implement_design.tcl # SYNTH → P&R → verify_timing → export
│       ├── 5_program_design.tcl   # generate .job
│       ├── src_components/        # per-IP create_and_configure_core calls
│       ├── src_constraints/       # .pdc and .sdc files
│       ├── src_hdl/               # custom Verilog (SSDetect.v from upstream + our additions)
│       └── src_softconsole/       # Mi-V firmware hex (precompiled iog_cdr.hex for now)
├── build.sh              # one-shot build wrapper
├── setup.sh              # pre-build environment checks
├── README.md
├── CLAUDE.md             # agent-loaded instructions for this repo
└── NOTES.md              # reproduction notes for the AN4623 patches
```

## Building

On the Hetzner box (or any machine with Libero v2024.2 + the vault populated
per `NOTES.md`):

```
./setup.sh        # verifies the license daemon and IP-vault state
./build.sh        # runs Libero, logs to /tmp/build_<timestamp>.log, sha256s the .job
```

Wall time: ~22 minutes (SYNTH 2.5 min, P&R 10 min, VERIFY/GEN/EXPORT 5 min).

### First-time setup on a fresh cloud VM

See [`docs/SETUP.md`](docs/SETUP.md) — step-by-step walkthrough from
"blank Ubuntu 24.04 cloud VM" to "can build this repo" (Libero download
+ install, license setup, lmgrd, libstdc++ workarounds, wrapper scripts,
swap, simulation toolchain). ~60–90 minutes of active work plus license
wait.

The resulting `.job` lands in `gateware/Libero_Project/designer/top/export/top.job`
(this directory is `.gitignored`; it's regenerated on every build).

## Flashing and testing

**Don't want to build?** Silicon-validated `.job` files are published on the
[Releases page](https://github.com/JamesPetrie/interlock/releases) with per-file
provenance (source commit, bucket config, validation results, sha256):

```
gh release download bitstreams-2026-07-20 -R JamesPetrie/interlock -p '*.job'
sha256sum *.job   # check against the release notes
```

Flashing needs **no Microchip license** — FlashPro Express is free (a
microchip.com account may be needed to download the installer). The Libero
Silver license in `docs/SETUP.md` is only for *building* bitstreams.

On a machine with FlashPro Express + USB connection to the board, the minimal
path is: `scp` the `.job` over, open FlashPro Express, load it, click RUN.

For the project's **DGX Spark bench** — where the board can't run Libero
directly (ARM64 host, x86-only tools) — the full flash + hardware-test workflow
is documented in [`docs/flashing-and-testing.md`](docs/flashing-and-testing.md),
with the runnable scripts (flash chain + data-plane tests: forwarding, TYPE-drop,
cert egress, beacon) version-controlled in [`bench/`](bench). See also `NOTES.md`
for the original scapy + tcpdump loopback pattern.

## License

The AN4623 source is from Microchip and retains its original licensing
(see `gateware/readme.txt` or Microchip's AN4623 page). Our additions are
MIT (or whatever — decide before going public).
