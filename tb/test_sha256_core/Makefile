# cocotb testbench for sha256_core.sv
TOPLEVEL_LANG    = verilog
TOPLEVEL         = sha256_core
MODULE           = test_sha256_core

REPO_ROOT       := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
VERILOG_SOURCES  = $(REPO_ROOT)/gateware/src/src_hdl/crypto/sha256_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/crypto/sha256_core.sv

include $(REPO_ROOT)/tb/common.mk
