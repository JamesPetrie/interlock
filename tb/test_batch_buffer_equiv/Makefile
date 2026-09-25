# cycle-exact equivalence of batch_buffer against its committed version (block-RAM inference rewrite)
TOPLEVEL_LANG    = verilog
TOPLEVEL         = tb_bb_equiv
MODULE           = test_bb_equiv
REPO_ROOT       := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
CORE            := $(REPO_ROOT)/gateware/src/src_hdl
VERILOG_SOURCES  = $(CORE)/eth_pkg.sv $(CORE)/batch_buffer.sv $(CURDIR)/batch_buffer_ref.sv $(CURDIR)/tb_bb_equiv.sv
SIM_BUILD        = sim_build_equiv
export PYTHONPATH := $(REPO_ROOT)/tb:$(PYTHONPATH)
include $(REPO_ROOT)/tb/common.mk
