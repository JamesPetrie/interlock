#!/bin/bash
# VPK180 interlock — Vivado flow wrapper (run on the machine with Vivado +
# Versal Premium device support + license; this box only has Zynq-7000).
#
#   ./build.sh exdes      # 1. generate the MRMAC example design from params.tcl
#   ./build.sh integrate  # 2. add the interlock RTL, print what to rewire
#   ./build.sh build      # 3. synth + impl + PDI  -> build/out/*.pdi
#   ./build.sh program [host:port]   # JTAG program via hw_server
set -eo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
VIVADO="${VIVADO:-vivado}"
LOG="$HERE/build/vivado_$(date +%Y%m%d_%H%M%S)_$1.log"
mkdir -p "$HERE/build"
case "${1:-}" in
  exdes)     "$VIVADO" -mode batch -nojournal -log "$LOG" -source "$HERE/scripts/gen_exdes.tcl" ;;
  integrate) "$VIVADO" -mode batch -nojournal -log "$LOG" -source "$HERE/scripts/integrate.tcl" ;;
  build)     "$VIVADO" -mode batch -nojournal -log "$LOG" -source "$HERE/scripts/build.tcl" ;;
  program)   "$VIVADO" -mode batch -nojournal -log "$LOG" -source "$HERE/scripts/program.tcl" -tclargs "${2:-localhost:3121}" ;;
  *) echo "usage: $0 {exdes|integrate|build|program [host:port]}" >&2; exit 1 ;;
esac
echo "log: $LOG"
