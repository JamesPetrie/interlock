# cocotb testbench for eth_deframe.sv
TOPLEVEL_LANG    = verilog
TOPLEVEL         = eth_deframe
MODULE           = test_eth_deframe

REPO_ROOT       := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
VERILOG_SOURCES  = $(REPO_ROOT)/gateware/src/src_hdl/eth_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/eth_deframe.sv

include $(REPO_ROOT)/tb/common.mk
