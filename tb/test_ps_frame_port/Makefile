# cocotb testbench for ps_frame_port (PS frame injector / capture, VPK180 port)
TOPLEVEL_LANG    = verilog
TOPLEVEL         = tb_ps_frame_port
MODULE           = test_ps_frame_port

REPO_ROOT       := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
HDL             := $(REPO_ROOT)/gateware_vpk180/hdl
VERILOG_SOURCES  = $(HDL)/axis_pkt_fifo.sv $(HDL)/sim_axis_cdc_fifo.sv $(HDL)/axis_downsize.sv $(HDL)/axis_upsize.sv \
                   $(HDL)/mrmac_axis_adapt.sv $(HDL)/ps_frame_port.sv $(CURDIR)/tb_ps_frame_port.sv
SIM_BUILD        = sim_build_psfp
COMPILE_ARGS    += -DSIM_NO_XPM
export PYTHONPATH := $(REPO_ROOT)/tb:$(PYTHONPATH)

include $(REPO_ROOT)/tb/common.mk
