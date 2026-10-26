# Fast firmware iteration via JTAG-load to TCM

```
TODO (James): Claude reviewed this doc (2026-09-18). The JTAG-load workflow it
describes is recorded as not working; update it or retire it.
```

This guide is for iterating on the Mi-V firmware (the soft RISC-V CPU
that initializes CoreTSE / VSC8575 on the MPF300-EVAL-KIT) without
having to rebuild and reflash the FPGA bitstream for every change.

Build cycle without this setup: ~30 minutes per firmware change
(22 min Libero rebuild + flash + test).

Build cycle with this setup: ~10 seconds per firmware change
(SoftConsole compile + JTAG-load + run).

## Why this works without a gateware change

The Mi-V's debug interface (`COREJTAGDEBUG_C0_0` in `top.tcl`,
`DEBUGGER:true` in `MIV_RV32_C0.tcl`) is already configured to expose
JTAG-driven memory access to TCM at `0x80000000`. SoftConsole's "Debug"
launch uses this path: halt CPU → write ELF program sections to TCM via
JTAG → set PC = `_start` → resume CPU. No bitstream rebuild needed.

The `iog_cdr.hex` baked into sNVM stays in place as the
power-on default — boot-up loads it as before. JTAG-load just overrides
TCM contents during a debug session.

## Prerequisites

- **Local machine**: Ubuntu x86_64 (native, or under emulation on
  macOS via UTM / VMware / Parallels).
- **Board**: MPF300-EVAL-KIT, connected to the local machine via the
  USB-FlashPro cable (FT4232H quad-channel).
- **Current bitstream flashed**: sub-PR #5 or later (any build with
  `DEBUGGER:true` Mi-V config — all of our builds so far).
- **Tools to install** (see below): SoftConsole, plus a USB serial
  terminal.

If you're on macOS with UTM, ensure USB passthrough is enabled for the
FPGA board: in UTM's VM settings, add the FTDI device (vendor id `0403`,
product id `6011` for the FT4232H) to the USB pass-through list. The
VM should then see the board as `/dev/ttyUSB0`, `/dev/ttyUSB1`,
`/dev/ttyUSB2`, `/dev/ttyUSB3` — one channel for JTAG, others for
auxiliary serial.

## Step 1: Install SoftConsole

1. Create a free Microchip account at https://www.microchip.com/login
2. Go to https://www.microchip.com/en-us/development-tool/softconsole
3. Download the latest **Linux x86_64** installer (`.run` file,
   ~600 MB). The naming pattern is:
   `Microchip-SoftConsole-vYYYY.M-RISC-V-XXX-linux-x64-installer.run`
4. Install Ubuntu prerequisites:
   ```
   sudo apt-get update
   sudo apt-get install \
       libgtk-3-0 libxtst6 libxrender1 libxi6 libgl1 \
       libwebkit2gtk-4.1-0 libusb-1.0-0 \
       build-essential
   ```
   On older Ubuntu the package is `libwebkit2gtk-4.0-37`; on 24.04 it's
   `libwebkit2gtk-4.1-0`. Either is fine; Eclipse only uses webkit for
   its help system.
5. Run the installer:
   ```
   chmod +x Microchip-SoftConsole-vYYYY.M-RISC-V-XXX-linux-x64-installer.run
   ./Microchip-SoftConsole-vYYYY.M-RISC-V-XXX-linux-x64-installer.run
   ```
   Default install path: `~/Microchip/SoftConsole-vYYYY.M-RISC-V/`.
6. Launch:
   ```
   ~/Microchip/SoftConsole-vYYYY.M-RISC-V/eclipse/eclipse
   ```
   On first launch, pick a workspace directory
   (e.g. `~/softconsole-workspace`).

## Step 2: Copy the AN4623 firmware source from Hetzner

The full SoftConsole project for the AN4623 firmware lives at
`/root/fpga/an4623/mpf_an4623_v2022p3_df/SoftConsole_Project/iog_cdr/`
on the Hetzner box. Copy it to your Ubuntu VM:

```
scp -r <hetzner>:/root/fpga/an4623/mpf_an4623_v2022p3_df ~/
```

This is ~50 MB. The relevant subtree is
`~/mpf_an4623_v2022p3_df/SoftConsole_Project/iog_cdr/`.

## Step 3: Import the project into SoftConsole

In SoftConsole: `File → Import → General → Existing Projects into
Workspace`. Browse to
`~/mpf_an4623_v2022p3_df/SoftConsole_Project/iog_cdr/`.
The existing `.project` and `.cproject` files let SoftConsole pick it
up without any conversion. Check the box for "Copy projects into
workspace" if you want a clean copy in your workspace folder.

## Step 4: Build the Debug configuration

The project ships with two build configurations:

- `Debug` (uses `microsemi-riscv-ram.ld`, links to `0x80000000` — TCM).
  **This is the one for JTAG-load.**
- `Release` (uses `run_from_nvm.ld`, links to eNVM addresses).

Select `Project → Build Configurations → Set Active → Debug`, then
`Project → Build All` (or Ctrl-B). The build runs the bundled
`riscv64-unknown-elf-gcc` against `main.c` + drivers; output appears
in `Debug/iog_cdr.elf` (and an `iog_cdr.hex` we won't use).

Build time on UTM x86 emulation: ~20–30 seconds.

## Step 5: Create a JTAG debug launch

In SoftConsole: `Run → Debug Configurations…` → right-click `GDB
OpenOCD Debugging` → `New Configuration`.

- **Main tab**: `C/C++ Application` = `Debug/iog_cdr.elf`, `Project`
  = `iog_cdr`.
- **Debugger tab**:
  - `OpenOCD Setup → Config options` should include the PolarFire JTAG
    config that SoftConsole ships. Typically:
    `-f board/microsemi-cortex-a9.cfg` doesn't apply — use the
    PolarFire-specific config that comes with SoftConsole's `openocd`
    directory. Look for a file like
    `share/openocd/scripts/board/microsemi-riscv-mpf300-eval-kit.cfg`
    or similar. (SoftConsole's installer adds it; if you can't find it,
    the SoftConsole "New Debug Configuration" wizard often pre-fills
    this correctly when given an `.elf` from a Mi-V project.)
  - `GDB Client Setup → Executable` should be
    `riscv64-unknown-elf-gdb` (bundled in SoftConsole).
- **Startup tab**:
  - ☑ "Reset target before download"
  - ☑ "Load image" (this is the JTAG write to TCM)
  - ☑ "Set program counter at entry point: `_start`"
  - ☑ "Set breakpoint at: `main`" (so the run halts at `main` and you
    can step / inspect, not run wild)

Save and click `Debug`. SoftConsole should:
1. Connect to the board via OpenOCD over JTAG.
2. Halt the Mi-V hart.
3. Write the ELF program sections to TCM at `0x80000000`.
4. Set PC to `_start` and reset hart state.
5. Run to `main()` and halt.

You can now step, inspect variables, set breakpoints, etc. Hit
`Resume` (F8) to let it run.

## Step 6: Read UART output

The Mi-V firmware can print over the on-board UART. The MPF300-EVAL-KIT
routes the UART through the same FTDI FT4232H chip as the JTAG —
typically the **3rd channel** (`/dev/ttyUSB2`) is the UART.

Open a serial terminal at **115200 baud, 8N1**:

```
screen /dev/ttyUSB2 115200
```

(Exit with `Ctrl-A`, `K`, `y`.)

Or `minicom -D /dev/ttyUSB2 -b 115200`.

To add prints in `main.c`, use the CoreUARTapb driver:

```c
#include "drivers/CoreUARTapb/core_uart_apb.h"

UART_instance_t g_uart;
UART_init(&g_uart, COREUARTAPB0_BASE_ADDR, BAUD_VALUE_115200,
          DATA_8_BITS | NO_PARITY);

UART_polled_tx_string(&g_uart, (const uint8_t *)"hello world\r\n");
```

The base address constant comes from the SoftConsole project's
`hw_platform.h`.

## Step 7: Iterate

Edit C → Ctrl-B (build) → F11 (debug launch). Each cycle is ~30 seconds
under emulation. The bitstream stays the same the entire time.

## Resetting the FPGA fabric (not just the CPU)

JTAG-load resets the CPU but **leaves the rest of the FPGA fabric in
whatever state the previous firmware left it in** — CoreTSE registers,
PHY autoneg state, FIFOs, etc.

To get a true cold-start (CoreTSE post-reset, PHY un-initialized, etc.)
before each debug run, **press the board's RESET_N button (the small
push-button labeled SW1 or similar, wired to pin K22)** right before
clicking `Debug` in SoftConsole. That pulses the fabric reset which
fans out through `Core_reset_pf_0`, resetting every core in the design.

For bring-up debugging this is usually what you want — you'll see the
init sequence run against a clean slate every time.

## Troubleshooting

- **OpenOCD can't connect**: confirm USB passthrough in UTM; confirm
  the board is powered (red LED on) and the JTAG channel is at
  `/dev/ttyUSB0` (typically the first FTDI channel). Try
  `lsusb | grep -i ftdi` inside the VM — you should see the
  `0403:6011` device.
- **"Cannot find target"**: SoftConsole's OpenOCD config needs to
  match the PolarFire JTAG chain. The right config file should be in
  `<SoftConsole>/openocd/share/openocd/scripts/board/`. If
  auto-detection fails, look at AN4997 §3.10 for the exact OpenOCD
  command line.
- **Firmware loads but doesn't run**: check that the linker script in
  use is `microsemi-riscv-ram.ld` (Debug config), not
  `run_from_nvm.ld`. Verify by inspecting `iog_cdr.map` — the `.text`
  section should be at `0x80000000`, not `0x60000000`.
- **No UART output**: verify baud rate (115200), wrong serial port
  (try `/dev/ttyUSB1`, `/dev/ttyUSB2`, `/dev/ttyUSB3` in turn), or the
  firmware isn't actually calling `UART_init` before the print.
- **CPU keeps re-running old firmware after JTAG-load**: confirm
  "Reset target before download" and "Set program counter at entry
  point" are both checked in the debug launch configuration.

## References

- MIV_RV32 v3.1 User Guide
  (https://ww1.microchip.com/downloads/aemDocuments/documents/FPGA/ProductDocuments/SupportingCollateral/MIV_RV32+v3.1+User+Guide.pdf)
  — §2.5 covers JTAG Debug Module / System Bus Access; §4.7.2 confirms
  JTAG writes to memory locations.
- AN4997: Building a Mi-V Subsystem
  (https://ww1.microchip.com/downloads/aemDocuments/documents/FPGA/ApplicationNotes/ApplicationNotes/PolarFire_FPGA_Building_MIV_Subsystem_AN4997.pdf)
  — §3.10 walks through the SoftConsole debug-launch workflow.
- AN4623 / DG0799: PolarFire 1G Ethernet Loopback Using IOD CDR
  (https://ww1.microchip.com/downloads/aemDocuments/documents/FPGA/ApplicationNotes/ApplicationNotes/microsemi_polarfire_fpga_1g_ethernet_iod_cdr_demo_guide_dg0799_v5.pdf)
  — source code for the firmware we're starting from.
