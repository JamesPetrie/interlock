# cocotb testbench for ilock_pl in pass-through mode (VPK180 port shim)
TOPLEVEL_LANG    = verilog
TOPLEVEL         = tb_ilock_pl
MODULE           = test_ilock_pl_passthru

REPO_ROOT       := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
HDL             := $(REPO_ROOT)/gateware_vpk180/hdl
CORE            := $(REPO_ROOT)/gateware/src/src_hdl
VERILOG_SOURCES  = $(HDL)/axis_pkt_fifo.sv $(HDL)/sim_axis_cdc_fifo.sv $(HDL)/axis_downsize.sv $(HDL)/axis_upsize.sv \
                   $(HDL)/axis2tse.sv $(HDL)/tse2axis.sv $(HDL)/mac_port_shim.sv $(HDL)/mrmac_axis_adapt.sv \
                   $(CORE)/pkt_counter.sv $(CORE)/sticky_bit.sv $(HDL)/ilock_pl.sv $(CURDIR)/tb_ilock_pl.sv
SIM_BUILD        = sim_build_passthru
COMPILE_ARGS    += -DSIM_NO_XPM
export PYTHONPATH := $(REPO_ROOT)/tb:$(PYTHONPATH)

include $(REPO_ROOT)/tb/common.mk
