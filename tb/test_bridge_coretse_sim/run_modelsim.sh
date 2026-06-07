#!/bin/bash
set -e
export PATH=/usr/local/microchip/Libero_SoC_v2024.2/ModelSim/linuxacoem:$PATH
export LM_LICENSE_FILE=/opt/microchip/licenses/License.dat
export MGLS_LICENSE_FILE=/opt/microchip/licenses/License.dat
cd "$(dirname "$0")"
SRC=../../gateware/src/src_hdl
CORE=/root/fpga/an4623/mpf_an4623_v2022p3_df/TCL_Scripts/Libero_Project/component/work/CORETSE_0/CORETSE_0_0/rtl/vlog/core_evaluation
W=/root/fpga/coretse_sim/work_bridge
rm -rf "$W"; vlib "$W" >/dev/null 2>&1; vmap work "$W" >/dev/null 2>&1
echo "=== compile ==="
vlog -sv -quiet +incdir+"$CORE" \
  $SRC/eth_pkg.sv $SRC/crc32_pkg.sv $SRC/eth_deframe.sv $SRC/eth_reframe.sv $SRC/fabric_bridge.sv \
  "$CORE/CoreTSE.v" tb_bridge_coretse.sv 2>&1 | grep -viE "Protect keyword" | tail -15
echo "=== simulate ==="
vsim -c -quiet work.tb_bridge_coretse -do "run -all; quit -f" 2>&1 \
  | grep -iE "FORWARDED|STALL|RESULT|DST:|SRC:|LEN:|configuring|injecting|reframe_req|deframe_req|a_mrx|b_mtx|Error|Fatal" \
  | grep -viE "vlog-|Protect keyword" | tail -40
