# cocotb testbench for cert_framer.v (cert bytes -> MAC-TX frame)
TOPLEVEL_LANG    = verilog
TOPLEVEL         = cert_framer
MODULE           = test_cert_framer

REPO_ROOT       := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
VERILOG_SOURCES  = $(REPO_ROOT)/gateware/src/src_hdl/eth_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/crc32_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/eth_reframe.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/core/bytes2axis32.v
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/core/cert_framer.v

include $(REPO_ROOT)/tb/common.mk
