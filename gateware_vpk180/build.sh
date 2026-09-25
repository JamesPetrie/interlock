#!/bin/bash
# VPK180 interlock — Vivado flow wrapper (run on the machine with Vivado +
# Versal Premium device support + license; this box only has Zynq-7000).
#
#   ./build.sh exdes      # 1. generate the MRMAC example design from params.tcl
#   ./build.sh integrate  # 2. add the interlock RTL, print what to rewire
#   ./build.sh link-test  # 2b. untouched example design on the cage quad -> build/link_test/
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
  link-test) "$VIVADO" -mode batch -nojournal -log "$LOG" -source "$HERE/scripts/link_test_build.tcl" ;;
  link-sw)   xsct "$HERE/scripts/vitis_link_test.tcl" ;;
  dcmac-exdes) "$VIVADO" -mode batch -nojournal -log "$LOG" -source "$HERE/scripts/gen_dcmac_exdes.tcl" ;;
  dcmac-test)  "$VIVADO" -mode batch -nojournal -log "$LOG" -source "$HERE/scripts/dcmac_link_test_build.tcl" ;;
  dcmac-sw)    xsct "$HERE/scripts/vitis_platform_app.tcl" "$HERE/build/dcmac_link_test/dcmac_link_test.xsa" "$HERE/build/dcmac_link_test/vitis" dcmac_test \
                 "$(ls -d "$HERE"/build/dcmac_exdes/*/*.gen/sources_1/ip/dcmac_0/sample_c_files | head -1)" ;;
  link-run)  bash "$HERE/scripts/link_run.sh" "${@:2}" ;;
  dual-link) "$VIVADO" -mode batch -nojournal -log "$LOG" -source "$HERE/scripts/dual_link_build.tcl" ;;
  dual-sw)   python3 "$HERE/tools/gen_dual_sw.py" "$(ls "$HERE"/build/exdes/*/*.gen/sources_1/ip/mrmac_0/sample_c_files/mrmac_exdes_test.c | head -1)" "$HERE/build/dual_link/sw" \
             && { xsct "$HERE/scripts/vitis_platform_app.tcl" "$HERE/build/dual_link/mrmac_dual_link.xsa" "$HERE/build/dual_link/vitis" mrmac_dual "$HERE/build/dual_link/sw" || true; } \
             && bash "$HERE/scripts/build_ps_app.sh" "$HERE/build/dual_link/mrmac_dual.elf" \
                  "$HERE/build/dual_link/vitis/plat_mrmac_dual_link/export/plat_mrmac_dual_link/sw/plat_mrmac_dual_link/standalone_domain" \
                  "$HERE/build/dual_link/vitis/mrmac_dual/src/lscript.ld" "$HERE/build/dual_link/sw/mrmac_dual_test.c" ;;
  inline-build) "$VIVADO" -mode batch -nojournal -log "$LOG" -source "$HERE/scripts/inline_build.tcl" -tclargs "${INLINE_NPORTS:-4}" "${INLINE_TOPO:-plain}" ;;   # INLINE_NPORTS=2: pass-through pair only; INLINE_TOPO=psdirect: PS frame ports + core
  inline-sw) # the PS side is identical to the two-port build (same CIPS block, same PL peripherals), so its BSP is reused
             python3 "$HERE/tools/gen_dual_sw.py" "$(ls "$HERE"/build/exdes/*/*.gen/sources_1/ip/mrmac_0/sample_c_files/mrmac_exdes_test.c | head -1)" "$HERE/build/inline/sw" "${INLINE_APP:-$([ "${INLINE_TOPO:-plain}" = psdirect ] && echo mrmac_psdirect_test.c || echo mrmac_inline_test.c)}" \
             && PSAPP_CFLAGS="-DNPORT=${INLINE_NPORTS:-4}" bash "$HERE/scripts/build_ps_app.sh" "$HERE/build/inline/mrmac_inline.elf" \
                  "$HERE/build/dual_link/vitis/plat_mrmac_dual_link/export/plat_mrmac_dual_link/sw/plat_mrmac_dual_link/standalone_domain" \
                  "$HERE/build/dual_link/vitis/mrmac_dual/src/lscript.ld" "$HERE/build/inline/sw/${INLINE_APP:-$([ "${INLINE_TOPO:-plain}" = psdirect ] && echo mrmac_psdirect_test.c || echo mrmac_inline_test.c)}" ;;   # INLINE_APP=<file in sw/> overrides
  inline-run) bash "$HERE/scripts/link_run.sh" "$(ls "$HERE"/build/inline/*.pdi | head -1)" "$HERE/build/inline/mrmac_inline.elf" "${2:-200}" ;;
  dual-run)  bash "$HERE/scripts/link_run.sh" "$(ls "$HERE"/build/dual_link/*.pdi | head -1)" "$HERE/build/dual_link/mrmac_dual.elf" "${2:-150}" ;;
  build)     "$VIVADO" -mode batch -nojournal -log "$LOG" -source "$HERE/scripts/build.tcl" ;;
  program)   "$VIVADO" -mode batch -nojournal -log "$LOG" -source "$HERE/scripts/program.tcl" -tclargs "${2:-localhost:3121}" ;;
  *) echo "usage: $0 {exdes|integrate|link-test|link-sw|link-run|dcmac-exdes|dcmac-test|dcmac-sw|dual-link|dual-sw|dual-run|inline-build|inline-sw|inline-run|build|program [host:port]}" >&2; exit 1 ;;
esac
echo "log: $LOG"
