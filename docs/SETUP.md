# Setting up the build environment on a fresh cloud Linux VM

This guide walks through everything needed to take a freshly-provisioned
Linux VM from "blank Ubuntu" to "can synthesize this repo's `.job` file".

Aimed at one-time setup — the steps below should not need to be repeated
once a VM is configured. Day-to-day usage is just `./build.sh` (see the
repo `README.md`).

The setup has been verified on Hetzner Cloud (CPX21, Ubuntu 24.04 LTS),
but nothing here is Hetzner-specific. Any cloud VM with the listed
specs and a Linux distro that can apt-install Verilog tooling should
work.

## Table of contents

1. [Hardware / VM requirements](#1-hardware--vm-requirements)
2. [Base OS setup](#2-base-os-setup)
3. [Libero SoC v2024.2](#3-libero-soc-v20242)
4. [License (lmgrd + license file)](#4-license-lmgrd--license-file)
5. [Post-install fixes for headless Ubuntu 24.04](#5-post-install-fixes-for-headless-ubuntu-2404)
6. [Wrapper scripts in `/usr/local/bin/`](#6-wrapper-scripts-in-usrlocalbin)
7. [Swap space](#7-swap-space)
8. [Simulation toolchain (for cocotb tests)](#8-simulation-toolchain-for-cocotb-tests)
9. [Clone the repo and run the first build](#9-clone-the-repo-and-run-the-first-build)
10. [Common gotchas](#10-common-gotchas)
11. [What's NOT covered](#11-whats-not-covered)
12. [Wrapper script contents (reference)](#12-wrapper-script-contents-reference)

---

## 1. Hardware / VM requirements

Resource | Minimum | Recommended
---|---|---
RAM | 8 GB + 8 GB swap | 16 GB (then swap optional)
Disk | 80 GB free | 150 GB
vCPU | 4 | 8+ (synthesis is mostly single-threaded but `make -j` helps)
Network | any | any
GPU | none | none

The 8 GB RAM minimum is real — PolarFire P&R for this repo peaks around
~1 GB of swap on an 8 GB system. Larger designs may push further. If
you're paying by RAM, 8 GB + 8 GB swap is the sweet spot.

Disk: Libero itself is ~30 GB installed, the v2024.2 web installer is ~250
MB, the program-debug install adds another ~1 GB, plus per-project build
outputs (~50–200 MB each). 80 GB is comfortable, 150 GB is generous.

x86_64 only. Libero v2024.2 does not have an ARM build, so Graviton /
DGX Spark (ARM64) do not work as build hosts. Build on x86_64, flash
from anywhere.

## 2. Base OS setup

Ubuntu 24.04 LTS is the assumed base. Other distros likely work but
Libero officially supports RHEL/CentOS — Ubuntu is the "tested unofficially"
case.

```bash
# Run as root (or with sudo) once at provisioning time
apt update
apt install -y \
    build-essential git curl wget unzip \
    xvfb \
    libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
    libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 \
    libxcb-xkb1 libxkbcommon-x11-0 libgl1 libegl1 \
    iverilog python3-venv \
    rsync openssh-client
```

The `libxcb-*` packages are runtime dependencies of Qt 5's `xcb` platform
plugin, which Libero pulls in even for CLI-only operations. Without them
Libero crashes at startup with `Could not load the Qt platform plugin
"xcb"`. The `xvfb` package gives us a fake X server so headless invocation
works at all.

## 3. Libero SoC v2024.2

You need a Microchip account ([microchip.com](https://www.microchip.com/),
free to register). Once logged in, download:

- **Libero SoC v2024.2 Linux Web installer** (~250 MB):
  Full install for synthesis (downloads full installer on the fly)
  https://www.microchip.com/en-us/products/fpgas-and-plds/fpga-and-soc-design-tools/fpga-software-downloads

- **Program and Debug v2024.2 (Linux)** (~1 GB):
  Separate FlashPro / SoftConsole pieces; small subset of tools for flashing the board on the local machince.
  Usefull when sythensys runs on a different machine and storage is limited.
  https://www.microchip.com/en-us/products/fpgas-and-plds/fpga-and-soc-design-tools/programming-and-debug/lab#tabs-0943f4d4b7-item-c864374c9e-tab

`scp` the installer to the VM, then:

```bash
mkdir -p /root/fpga
cd /root/fpga
unzip Libero_SoC_v2024.2_Web_lin.zip            # extracts a .bin
chmod +x Libero_SoC_v2024.2_Web_lin.bin

# Run the installer — uses the Java installer UI; pass --mode silent
# to skip the GUI (requires an installer config file) or accept the
# default GUI mode via xvfb-run for one-time install:
xvfb-run -a ./Libero_SoC_v2024.2_Web_lin.bin
```

Default install location: `/usr/local/microchip/Libero_SoC_v2024.2/`.
Leave it there unless you have a strong reason; the wrapper scripts and
playbooks assume this path.

The installer wants ~5 GB of disk during install. Plan for it.

## 4. License (lmgrd + license file)

Libero needs a license file ("Silver", "Gold", or "Platinum" tier). The
**Silver** tier is free and supports the MPF300 family this repo targets.

### Getting the license

1. Find the VM's MAC address: `cat /sys/class/net/eth0/address`. This
   needs to match exactly when the license is generated. Hetzner does NOT
   typically reassign MACs, but other clouds might — pin the VM if you can.
2. Sign in to [microchipdirect.com](https://www.microchipdirect.com/fpga-software-products),
   request a "Libero Silver 1Yr Floating License for Windows/Linux Server" (free for evaluation
   purposes; supports MPF300TS-1FCG1152I).
3. Paste the VM's MAC during the request; Microchip emails the `.dat`
   license file within minutes.

Place the license file at `/opt/microchip/licenses/License.dat`:

```bash
mkdir -p /opt/microchip/licenses
# scp/paste your License.dat from email into that path
chmod 644 /opt/microchip/licenses/License.dat
```

The license file ships with a placeholder hostname (e.g.,
`<put.hostname.here>`). Edit it to `this_host`:

```bash
sed -i 's|<put.hostname.here>|this_host|' /opt/microchip/licenses/License.dat
head -1 /opt/microchip/licenses/License.dat
# Expected: SERVER this_host <MAC> 1702
```

### Starting lmgrd

```bash
# /usr/tmp is needed by FlexLM but Ubuntu 24.04 doesn't ship it
[ -d /usr/tmp ] || ln -s /tmp /usr/tmp

# Start the daemon — runs in the background, logs to /var/log/lmgrd.log
/usr/local/microchip/Libero_SoC_v2024.2/Libero/bin64/lmgrd \
    -c /opt/microchip/licenses/License.dat \
    -l /var/log/lmgrd.log

# Verify it's listening
ss -tlnp | grep 1702
# Expected: a row showing port 1702 owned by lmgrd / actlmgrd / saltd / snpslmd
```

**Note: lmgrd started this way won't survive a reboot**, and this has
actually bitten: after an unnoticed VM reboot, `./build.sh` fails within
seconds with `Cannot locate license file` (see gotchas, §10). Put it
under systemd so it comes back on its own:

```bash
cat > /etc/systemd/system/lmgrd.service <<'EOF'
[Unit]
Description=FlexLM license daemon for Libero SoC
After=network.target

[Service]
Type=forking
ExecStart=/usr/local/microchip/Libero_SoC_v2024.2/Libero/bin64/lmgrd \
    -c /opt/microchip/licenses/License.dat \
    -l /var/log/lmgrd.log
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now lmgrd.service
systemctl status lmgrd.service --no-pager   # should show active, port 1702 listening
```

(If you started a bare `lmgrd` earlier in this section, kill it first —
`pkill lmgrd` — or the systemd instance will fail to bind port 1702.)

### Test license checkout

```bash
# Should print version info + license info without errors
/usr/local/microchip/Libero_SoC_v2024.2/Libero/bin64/lmutil lmstat -a -c 1702@localhost
```

## 5. Post-install fixes for headless Ubuntu 24.04

Libero v2024.2 ships some libraries that are too old for Ubuntu 24.04.
Two `libstdc++.so.6` shipped versions cap at `GLIBCXX_3.4.28`, but Ubuntu
24.04's `libicuuc.so.74` (pulled in transitively by Qt) requires
`GLIBCXX_3.4.30`. The fix: disable the bundled libs so the system one
(`GLIBCXX_3.4.33` on 24.04) is loaded instead.

```bash
mv /usr/local/microchip/Libero_SoC_v2024.2/Libero/lib64/rhel/libstdc++.so.6 \
   /usr/local/microchip/Libero_SoC_v2024.2/Libero/lib64/rhel/libstdc++.so.6.disabled

mv /usr/local/microchip/Libero_SoC_v2024.2/SynplifyPro/linux_a_64/lib/libstdc++.so.6 \
   /usr/local/microchip/Libero_SoC_v2024.2/SynplifyPro/linux_a_64/lib/libstdc++.so.6.disabled
```

If you later see `version 'GLIBCXX_3.4.30' not found` from another
Microchip sub-tool (e.g. `Identify`, `QuestaSim`), do the same for that
tool's bundled `libstdc++.so.6` — the pattern is identical.

## 6. Wrapper scripts in `/usr/local/bin/`

The Libero binaries pull in Qt and want a display, even for CLI-only
invocations. Wrappers in `/usr/local/bin/` use `xvfb-run` to provide a
fake one, and bake in the license-file environment variable so the
wrappers work in non-interactive shells (cron, scripts, agent tools)
that don't source `~/.bashrc`.

Create these four wrappers (full contents are in
[§12 Wrapper script contents](#12-wrapper-script-contents-reference)).
`chmod +x` each one.

```bash
/usr/local/bin/libero         # main entry point (xvfb wrapped)
/usr/local/bin/acttclsh       # Tcl shell (no xvfb needed)
/usr/local/bin/fpgenprog      # programming file generator (xvfb wrapped)
/usr/local/bin/FPExpress      # FlashPro Express (xvfb wrapped; unused on build VMs)
```

Verify the wrappers come first on `PATH`:

```bash
which libero
# Expected: /usr/local/bin/libero
```

### Gotcha: `libero -v` hangs

Do **not** use `libero -v` to check the version. The `-v` flag is not
"print version and exit" — it puts Libero into a Qt event-loop `poll()`
and hangs indefinitely. For a smoke test, use the `SCRIPT:` form:

```bash
cat > /tmp/smoke.tcl <<EOF
puts "Libero is alive"
puts "Tcl version: [info tclversion]"
exit
EOF
libero SCRIPT:/tmp/smoke.tcl
# Expected: Console Mode = Libero is alive, then "Execute Script command succeeded"
```

## 7. Swap space

Synthesis + P&R for the designs in this repo peak around ~1 GB of swap
on an 8 GB system. Pre-create 8 GB of swap so the build doesn't OOM:

```bash
fallocate -l 8G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile

# Persist across reboots
grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
swapon --show
free -h
```

## 8. Simulation toolchain (for cocotb tests)

Skip this section if you only want to build bitstreams. Needed for the
cocotb unit tests in `tb/`.

```bash
# Icarus Verilog as the simulator (cocotb 2.x needs Verilator ≥5.036
# and Ubuntu 24.04 ships 5.020, so icarus is the path of least resistance).
apt install -y iverilog

# Per-repo Python venv with cocotb + scapy (PEP 668 prevents system-pip
# from installing user packages on 24.04). The `make .venv` target in
# the repo handles this on first `make test`.
```

`make test` from the repo root will create the venv on first run; no
extra action needed beyond `apt install iverilog`.

## 9. Clone the repo and run the first build

### GitHub access

If the repo is private, set up a personal access token. The repo expects
the credential-helper pattern documented in `/root/CLAUDE.md` (token in
`~/.config/interlock/token` with `chmod 600`, credentials in
`~/.config/interlock/credentials` in git-credential-store format, per-repo
`credential.helper` pointing at that file). This keeps the token out of
the git remote URL.

```bash
mkdir -p ~/.config/interlock
echo 'github_pat_xxxxxxxxxxx' > ~/.config/interlock/token
chmod 600 ~/.config/interlock/token

cat > ~/.config/interlock/credentials <<EOF
https://JamesPetrie:$(cat ~/.config/interlock/token)@github.com
EOF
chmod 600 ~/.config/interlock/credentials

# Clone the repo with a clean (no-token) remote URL
git clone https://github.com/JamesPetrie/interlock.git /root/fpga/interlock
cd /root/fpga/interlock

# Per-repo override of any system-wide gh-auth credential helper
git config --local --add credential.https://github.com.helper ""
git config --local --add credential.https://github.com.helper \
    "store --file=$HOME/.config/interlock/credentials"
```

### Run the sanity check

```bash
cd /root/fpga/interlock
./setup.sh
```

Expected: 11/11 checks pass (Libero wrappers, lmgrd listening, libstdc++
patches in place, swap configured). If anything fails, the message
tells you what's missing — go back to the relevant section above.

### First build

```bash
./build.sh
```

Expected wall-clock time on Hetzner CPX21 (8 GB RAM, 4 vCPU):
**~22 minutes** for the baseline AN4623 design. The build runs:

1. Component download + SmartDesign elaboration (~30s)
2. Synthesis via Synplify Pro (~2.5 min)
3. Place-and-route (~10 min)
4. Verify timing + generate bitstream + export `.job` (~3 min)

The `.job` file lands in
`gateware/Libero_Project/designer/top/export/top.job`, ~12 MB.
`./build.sh` prints its SHA-256 at the end.

### Which design did I just build?

`main` currently builds the **recomp** top (`recomp_ilock_core`, since
commit `66a7c3c`) with a **100 ms testing-override bucket period**
(commit `a3376e3`). On a recomp build, sync + cert traffic appears on
**port 0 only** and only the 0→1 direction is processed — **a silent
port 1 is expected, not a broken setup.**

The production top (bidirectional `fabric_bridge` wiring, sync both
ways) at the real 1 ms bucket period is branch **`build/prod-1ms`**
(validated on silicon 2026-07-20). Build-time selection knobs
(`TOP={recomp,prod}`, `BUCKET_MS={100,1}`) exist on
`feature/build-config-knobs`, not yet merged.

### Tests (separate from synthesis)

```bash
make test    # cocotb unit tests, ~1s
```

Synthesis and simulation are independent — tests don't need Libero.

## 10. Common gotchas

### "Cannot find Spirit core configuration file" during synthesis

The pinned IP version is in Libero's catalog but its on-CDN package is
broken (`download_core` succeeds but only extracts `core_xml.zip`, not
the TCL filesets). Fix: bump to the next-newer version in the catalog.

Remember to edit **both places**:
1. `gateware/common.tcl` (the variable definition)
2. `gateware/src/src_components/<core>.tcl` (the literal in
   `create_and_configure_core`)

This is described in detail in
[`NOTES.md`](../NOTES.md#recovery-if-the-vault-gets-confused).

### `GLIBCXX_3.4.30' not found` from a Microchip sub-tool

A bundled `libstdc++.so.6` got loaded instead of the system one. Find
it with:

```bash
find /usr/local/microchip/Libero_SoC_v2024.2 -name 'libstdc++.so.6' -not -name '*.disabled'
```

Rename each to `.disabled`.

### lmgrd not running after reboot

**Symptom:** `./build.sh` fails within seconds and the log ends with
`Cannot locate license file` (or `lmutil lmstat` can't reach
`1702@localhost`). Almost always means the VM rebooted and lmgrd wasn't
under systemd (§4 has the unit file). One-off restart:

```bash
ss -tlnp | grep 1702 || \
  /usr/local/microchip/Libero_SoC_v2024.2/Libero/bin64/lmgrd \
    -c /opt/microchip/licenses/License.dat \
    -l /var/log/lmgrd.log
```

### Build fails fast at "Globals Assigner could not find a solution"

Too many IOD CDR CCCs / clock-network conflicts. Probably means two
`PF_IOD_CDR_CCC` instances on the same device edge — share a single
CCC instead (see sub-PR #3's commit message for the rationale).

### Build fails fast at "PDCPF-13: Illegal or Invalid assignment to Package pin"

LVDS polarity assignment that Libero rejects. Most often: try the
canonical P→PB / N→NB convention (the AN4623 baseline uses inverted
polarity that works for some pairs but not all).

### My PAT can't push: 403

If a `gh auth git-credential` helper is set globally, it overrides the
per-repo credential file. Clear the global helper for this repo:

```bash
git config --local --add credential.https://github.com.helper ""
git config --local --add credential.https://github.com.helper \
    "store --file=$HOME/.config/interlock/credentials"
```

## 11. What's NOT covered

- **FlashPro Express**: runs on the machine physically connected to the
  board via USB. Build VM doesn't need it. Microchip ships a separate
  installer. The full flash + hardware-test procedure for the project's
  DGX Spark bench (including the x86-QEMU-on-ARM64 flash path) is in
  [`flashing-and-testing.md`](flashing-and-testing.md), with the scripts in
  [`../bench/`](../bench).
- **SoftConsole / RISC-V firmware**: the precompiled Mi-V firmware
  (`iog_cdr.hex`) lives in `gateware/src/src_softconsole/` and is baked
  into the bitstream by Libero. Rebuilding the `.hex` needs a
  `riscv32-unknown-elf-gcc` toolchain or Microchip's SoftConsole IDE —
  documented separately if/when needed.
- **GUI usage**: Libero's GUI runs under `xvfb` but is impractically slow
  over SSH. Use the Tcl/CLI flow this repo is built around.
- **Multi-user setup**: this guide assumes one user, root-or-equivalent.
  Multi-user FlexLM with cached licenses is its own problem.

## 12. Wrapper script contents (reference)

The four wrapper scripts in `/usr/local/bin/`. Each is a few lines.
`chmod +x` after creating.

### `/usr/local/bin/libero`

```bash
#!/bin/bash
# Headless Libero SoC launcher. xvfb-run gives Qt a display.
export LM_LICENSE_FILE="${LM_LICENSE_FILE:-/opt/microchip/licenses/License.dat}"
export SNPSLMD_LICENSE_FILE="${SNPSLMD_LICENSE_FILE:-/opt/microchip/licenses/License.dat}"
exec /usr/bin/xvfb-run -a /usr/local/microchip/Libero_SoC_v2024.2/Libero/bin64/libero "$@"
```

### `/usr/local/bin/acttclsh`

```bash
#!/bin/bash
# Non-GUI Tcl shell, no xvfb needed.
export LM_LICENSE_FILE="${LM_LICENSE_FILE:-/opt/microchip/licenses/License.dat}"
export SNPSLMD_LICENSE_FILE="${SNPSLMD_LICENSE_FILE:-/opt/microchip/licenses/License.dat}"
exec /usr/local/microchip/Libero_SoC_v2024.2/Libero/bin64/acttclsh "$@"
```

### `/usr/local/bin/fpgenprog`

```bash
#!/bin/bash
# Programming file generator.
export LM_LICENSE_FILE="${LM_LICENSE_FILE:-/opt/microchip/licenses/License.dat}"
export SNPSLMD_LICENSE_FILE="${SNPSLMD_LICENSE_FILE:-/opt/microchip/licenses/License.dat}"
exec /usr/bin/xvfb-run -a /usr/local/microchip/Libero_SoC_v2024.2/Libero/bin64/fpgenprog "$@"
```

### `/usr/local/bin/FPExpress`

```bash
#!/bin/bash
# FlashPro Express launcher. Build VMs usually don't use this — flash
# happens on the machine physically connected to the board.
export LM_LICENSE_FILE="${LM_LICENSE_FILE:-/opt/microchip/licenses/License.dat}"
export SNPSLMD_LICENSE_FILE="${SNPSLMD_LICENSE_FILE:-/opt/microchip/licenses/License.dat}"
exec /usr/bin/xvfb-run -a /usr/local/microchip/Libero_SoC_v2024.2/Libero/bin64/FPExpress "$@"
```

## Time budget for the whole setup

End-to-end, on a fresh Hetzner CPX21 with reasonable internet:

| Step | Time |
|---|---|
| §2 Base OS setup | 5 min |
| §3 Libero download + install | 20 min |
| §4 License request → file in hand | varies (typically <30 min if you do it during business hours; can be longer) |
| §4 lmgrd start + verify | 2 min |
| §5 libstdc++ workarounds | 1 min |
| §6 Wrapper scripts | 5 min |
| §7 Swap | 1 min |
| §8 Sim toolchain | 5 min |
| §9 Clone + first `setup.sh` | 1 min |
| §9 First `build.sh` | 22 min |
| **Total** | **~60–90 min** of active work, plus license wait |

If you find your setup is taking significantly longer, something
upstream is probably wrong — re-read the relevant section.
