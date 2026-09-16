# cocotb testbench for sha256_msg.sv
TOPLEVEL_LANG    = verilog
TOPLEVEL         = sha256_msg
MODULE           = test_sha256_msg

REPO_ROOT       := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
VERILOG_SOURCES  = $(REPO_ROOT)/gateware/src/src_hdl/crypto/sha256_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/crypto/sha256_core.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/crypto/sha256_msg.sv

include $(REPO_ROOT)/tb/common.mk
