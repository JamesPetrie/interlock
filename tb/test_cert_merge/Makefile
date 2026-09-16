# cocotb testbench for cert_merge.sv
TOPLEVEL_LANG    = verilog
TOPLEVEL         = cert_merge
MODULE           = test_cert_merge

REPO_ROOT       := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
VERILOG_SOURCES  = $(REPO_ROOT)/gateware/src/src_hdl/cert_merge.sv

include $(REPO_ROOT)/tb/common.mk
