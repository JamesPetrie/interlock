# cocotb testbench for mac_port_shim (VPK180 port shim)
TOPLEVEL_LANG    = verilog
TOPLEVEL         = mac_port_shim
MODULE           = test_mac_port_shim

REPO_ROOT       := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
HDL             := $(REPO_ROOT)/gateware_vpk180/hdl
W               ?= 64
DEPTH           ?= 64
VERILOG_SOURCES  = $(HDL)/axis_pkt_fifo.sv $(HDL)/sim_axis_cdc_fifo.sv $(HDL)/axis_downsize.sv $(HDL)/axis_upsize.sv $(HDL)/axis2tse.sv $(HDL)/tse2axis.sv $(HDL)/mac_port_shim.sv
# one build dir per parameter set: -P overrides are not make dependencies
SIM_BUILD        = sim_build_W$(W)_D$(DEPTH)
COMPILE_ARGS    += -DSIM_NO_XPM -P $(TOPLEVEL).W=$(W) -P $(TOPLEVEL).RX_FIFO_DEPTH=$(DEPTH)
export PYTHONPATH := $(REPO_ROOT)/tb:$(PYTHONPATH)
export W DEPTH

include $(REPO_ROOT)/tb/common.mk
