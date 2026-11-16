# cocotb testbench for canon_proc.sv
TOPLEVEL_LANG    = verilog
TOPLEVEL         = canon_proc
MODULE           = test_canon_proc

REPO_ROOT       := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
# Packages first, in dependency order (canon_pkg references eth_pkg).
VERILOG_SOURCES  = $(REPO_ROOT)/gateware/src/src_hdl/eth_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/canon_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/crypto/sha256_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/serializer.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/canon_proc.sv

include $(REPO_ROOT)/tb/common.mk
