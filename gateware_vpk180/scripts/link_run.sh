#!/bin/bash
# Reprogram + run ritual for the VPK180 over JTAG, from the build machine:
#   scripts/link_run.sh [pdi] [elf] [console-seconds]
# 1. power-on reset through the system controller (a running PLM rejects a
#    new PDI over JTAG: "Image Header Table Validation failed"), 2. restart
#    hw_server (the FT4232H re-enumerates on that reset), 3. read the JTAG
#    mux select GPIOs (releases them to the board pulls; sc_app's
#    setJTAGselect drives them and the chain reads all ones), 4. xsdb: wait
#    for the Versal, program, load + run the ELF on A72 #0, 5. print the
#    Versal UART0 (ttyUSB1) console captured from before the reset.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(dirname "$HERE")"
PDI="${1:-$(ls "$ROOT"/build/link_test/*.pdi | head -1)}"
ELF="${2:-$(ls "$ROOT"/build/link_test/vitis/mrmac_test/Debug/*.elf | head -1)}"
SECS="${3:-90}"
set +u; source ~/Xilinx/2025.2/Vitis/settings64.sh; set -u   # the settings file reads unset vars
LOG="$ROOT/build/console_$(date +%H%M%S).log"
[ -f "$ROOT/build/uart.pid" ] && kill "$(cat "$ROOT/build/uart.pid")" 2>/dev/null
python3 "$HERE/../tools/uart_cat.py" /dev/ttyUSB1 "$SECS" > "$LOG" 2>&1 & echo $! > "$ROOT/build/uart.pid"
echo "### system controller: reset"
python3 "$HERE/../tools/sc_console.py" -t 20 "sc_app -c resetbootPDI >/dev/null 2>&1; sc_app -c reset; echo RESET_OK" 2>&1 | grep -E "RESET_OK|ERROR"
sleep 6
for p in $(pgrep -f "hw_serve[r]"); do kill "$p" 2>/dev/null; done; sleep 2
echo "### system controller: release JTAG mux select lines"
python3 "$HERE/../tools/sc_console.py" -t 20 "sc_app -c getgpio -t SYSCTLR_JTAG_S0 >/dev/null; sc_app -c getgpio -t SYSCTLR_JTAG_S1 >/dev/null; echo GPIO_OK" 2>&1 | grep -E "GPIO_OK|ERROR"
sleep 2
echo "### xsdb: program $PDI, run $ELF"
xsdb "$HERE/run_link_test.tcl" "$PDI" "$ELF" 2>&1 | grep -E "^###|rror" | cut -c1-140
echo "### console (waiting ${SECS}s total capture)"
wait "$(cat "$ROOT/build/uart.pid")" 2>/dev/null
strings "$LOG" | grep -v "^\s*$" | sed -n '/Platform Loader/,$p' | cut -c1-120
echo "### full log: $LOG"
