#!/bin/bash
# Compile + link a standalone A72 program against a platform's BSP without the
# Vitis workspace builder (whose generated makefile lacks the include/lib paths).
#   scripts/build_ps_app.sh <out.elf> <bsp-domain-dir> <lscript.ld> <src.c> [more.c ...]
# bsp-domain-dir = .../export/<plat>/sw/<plat>/standalone_domain (has bspinclude/ and bsplib/)
set -eu
OUT="$1"; DOM="$2"; LD="$3"; shift 3
set +u; source ~/Xilinx/2025.2/Vitis/settings64.sh; set -u   # the settings file reads unset vars
OBJS=()
for SRC in "$@"; do
  O="$(mktemp /tmp/psapp_XXXX.o)"
  aarch64-none-elf-gcc -Wall -O0 -g3 ${PSAPP_CFLAGS:-} -c -mcpu=cortex-a72 -I"$DOM/bspinclude/include" -I"$(dirname "$SRC")" -o "$O" "$SRC"
  OBJS+=("$O")
done
aarch64-none-elf-gcc -mcpu=cortex-a72 -Wl,-T -Wl,"$LD" -L"$DOM/bsplib/lib" -o "$OUT" "${OBJS[@]}" \
  -Wl,--start-group,-lxil,-lgcc,-lc,--end-group
rm -f "${OBJS[@]}"
echo "built $OUT ($(stat -c %s "$OUT") bytes)"
