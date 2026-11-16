# cocotb testbench for log2_iter.sv
TOPLEVEL_LANG    = verilog
TOPLEVEL         = log2_iter
MODULE           = test_log2_iter

REPO_ROOT       := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
VERILOG_SOURCES  = $(REPO_ROOT)/gateware/src/src_hdl/recomp_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/log2_iter.sv

include $(REPO_ROOT)/tb/common.mk
