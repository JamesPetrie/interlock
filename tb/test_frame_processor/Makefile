# cocotb testbench for frame_processor.sv
TOPLEVEL_LANG    = verilog
TOPLEVEL         = frame_processor
MODULE           = test_frame_processor

REPO_ROOT       := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
# Packages first, in dependency order (canon_pkg references eth_pkg).
VERILOG_SOURCES  = $(REPO_ROOT)/gateware/src/src_hdl/eth_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/canon_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/frame_processor.sv

include $(REPO_ROOT)/tb/common.mk
