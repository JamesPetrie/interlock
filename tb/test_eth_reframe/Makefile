# cocotb testbench for eth_reframe.sv
TOPLEVEL_LANG    = verilog
TOPLEVEL         = eth_reframe
MODULE           = test_eth_reframe

REPO_ROOT       := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
VERILOG_SOURCES  = $(REPO_ROOT)/gateware/src/src_hdl/eth_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/crc32_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/eth_reframe.sv

include $(REPO_ROOT)/tb/common.mk
