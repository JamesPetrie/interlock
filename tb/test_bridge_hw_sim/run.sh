#!/bin/bash
set -e
cd "$(dirname "$0")"
SRC="../../gateware/src/src_hdl"
FILES="$SRC/eth_pkg.sv $SRC/crc32_pkg.sv $SRC/eth_deframe.sv $SRC/eth_reframe.sv $SRC/fabric_bridge.sv tb_bridge_hw.sv"
echo "######## CONTROL B: DIRECT cross-wire (must FORWARD) ########"
iverilog -g2012 -DDIRECT -o /tmp/tb_direct $FILES && vvp /tmp/tb_direct
echo; echo "######## DUT: fabric_bridge sanitizer ########"
iverilog -g2012 -o /tmp/tb_bridge $FILES && vvp /tmp/tb_bridge
