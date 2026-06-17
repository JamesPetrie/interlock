# cocotb testbench for tick_beacon.v (bucket_tick -> 32B beacon byte stream)
TOPLEVEL_LANG    = verilog
TOPLEVEL         = tick_beacon
MODULE           = test_tick_beacon

REPO_ROOT       := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
VERILOG_SOURCES  = $(REPO_ROOT)/gateware/src/core/tick_beacon.v

include $(REPO_ROOT)/tb/common.mk
