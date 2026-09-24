# cocotb testbench for tb_tse2axis (VPK180 port shim)
TOPLEVEL_LANG    = verilog
TOPLEVEL         = tb_tse2axis
MODULE           = test_tse2axis

REPO_ROOT       := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
HDL             := $(REPO_ROOT)/gateware_vpk180/hdl
W               ?= 64
DEPTH           ?= 64
VERILOG_SOURCES  = $(HDL)/tse2axis.sv $(CURDIR)/tb_tse2axis.sv
# one build dir per parameter set: -P overrides are not make dependencies
SIM_BUILD        = sim_build_W$(W)_D$(DEPTH)
COMPILE_ARGS    += 
export PYTHONPATH := $(REPO_ROOT)/tb:$(PYTHONPATH)
export W DEPTH

include $(REPO_ROOT)/tb/common.mk
