# cocotb testbench for fabric_bridge.sv
TOPLEVEL_LANG    = verilog
TOPLEVEL         = fabric_bridge
MODULE           = test_fabric_bridge

REPO_ROOT       := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
VERILOG_SOURCES  = $(REPO_ROOT)/gateware/src/src_hdl/fabric_bridge.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/fcs_recalc.sv

include $(REPO_ROOT)/tb/common.mk
