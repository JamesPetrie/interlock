# cocotb testbench: ilock_pl with the production core AND the runtime pass-through switch
TOPLEVEL_LANG    = verilog
TOPLEVEL         = tb_ilock_pl
MODULE           = test_ilock_pl_bypass

REPO_ROOT       := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
HDL             := $(REPO_ROOT)/gateware_vpk180/hdl
CORE_VC         := $(REPO_ROOT)/gateware_vpk180/core_sources.vc
VERILOG_SOURCES  = $(addprefix $(REPO_ROOT)/gateware_vpk180/, $(shell cat $(CORE_VC))) \
                   $(HDL)/axis_pkt_fifo.sv $(HDL)/sim_axis_cdc_fifo.sv $(HDL)/axis_downsize.sv $(HDL)/axis_upsize.sv \
                   $(HDL)/axis2tse.sv $(HDL)/tse2axis.sv $(HDL)/mac_port_shim.sv $(HDL)/mrmac_axis_adapt.sv \
                   $(HDL)/ilock_pl.sv $(REPO_ROOT)/tb/test_ilock_pl_passthru/tb_ilock_pl.sv
SIM_BUILD        = sim_build_bypass
COMPILE_ARGS    += -DSIM_NO_XPM -P tb_ilock_pl.TOP_KIND=1 -P tb_ilock_pl.RUNTIME_BYPASS=1
export PYTHONPATH := $(REPO_ROOT)/tb:$(REPO_ROOT)/tb/test_ilock_pl_passthru:$(PYTHONPATH)

include $(REPO_ROOT)/tb/common.mk
