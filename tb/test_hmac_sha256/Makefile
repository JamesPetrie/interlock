# cocotb testbench for hmac_sha256.sv
TOPLEVEL_LANG    = verilog
TOPLEVEL         = hmac_sha256
MODULE           = test_hmac_sha256

REPO_ROOT       := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
VERILOG_SOURCES  = $(REPO_ROOT)/gateware/src/src_hdl/crypto/sha256_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/crypto/sha256_core.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/crypto/sha256_msg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/crypto/hmac_sha256.sv

include $(REPO_ROOT)/tb/common.mk
