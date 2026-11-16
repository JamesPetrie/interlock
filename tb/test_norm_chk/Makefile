# cocotb testbench for norm_chk.sv
TOPLEVEL_LANG    = verilog
TOPLEVEL         = norm_chk
MODULE           = test_norm_chk

REPO_ROOT       := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))/../..)

# tell the TB the token width (norm_chk's catch-all SPACE_W = 8x this);
# derived from canon_pkg so the bench follows the package (env override wins)
TOK_BYTES ?= $(shell sed -n 's/.*CANON_TOK_BYTES *= *\([0-9]*\);.*/\1/p' \
               $(REPO_ROOT)/gateware/src/src_hdl/canon_pkg.sv)
export TOK_BYTES

VERILOG_SOURCES  = $(REPO_ROOT)/gateware/src/src_hdl/eth_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/canon_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/recomp_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/norm_chk.sv

include $(REPO_ROOT)/tb/common.mk
