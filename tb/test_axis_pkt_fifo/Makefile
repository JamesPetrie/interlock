# cocotb testbench for axis_pkt_fifo (VPK180 port shim)
TOPLEVEL_LANG    = verilog
TOPLEVEL         = axis_pkt_fifo
MODULE           = test_axis_pkt_fifo

REPO_ROOT       := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
HDL             := $(REPO_ROOT)/gateware_vpk180/hdl
W               ?= 64
DEPTH           ?= 64
VERILOG_SOURCES  = $(HDL)/axis_pkt_fifo.sv
# one build dir per parameter set: -P overrides are not make dependencies
SIM_BUILD        = sim_build_W$(W)_D$(DEPTH)
COMPILE_ARGS    += -P $(TOPLEVEL).W=$(W) -P $(TOPLEVEL).DEPTH=$(DEPTH)
export PYTHONPATH := $(REPO_ROOT)/tb:$(PYTHONPATH)
export W DEPTH

include $(REPO_ROOT)/tb/common.mk
