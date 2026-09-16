# cocotb testbench for recomp_feed.sv
#
# Token-width sweep: CANON_TOK_BYTES is a canon_pkg localparam, so build the
# DUT against a patched copy of the package and tell the TB via TOK_BYTES:
#
#   sed 's/CANON_TOK_BYTES   =    4/CANON_TOK_BYTES   =    2/' \
#       ../../gateware/src/src_hdl/canon_pkg.sv > /tmp/canon_pkg_tok2.sv
#   TOK_BYTES=2 make SIM_BUILD=sim_build_tok2 VERILOG_SOURCES="\
#       $(REPO_ROOT)/gateware/src/src_hdl/eth_pkg.sv /tmp/canon_pkg_tok2.sv \
#       $(REPO_ROOT)/gateware/src/src_hdl/recomp_feed.sv"
#
# (same for 3; plain `make` runs the in-tree width, 4)
TOPLEVEL_LANG    = verilog
TOPLEVEL         = recomp_feed
MODULE           = test_recomp_feed

REPO_ROOT       := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))/../..)

# tell the TB the token width the DUT is compiled with (env override wins,
# e.g. for the width sweep against a patched package copy)
TOK_BYTES ?= $(shell sed -n 's/.*CANON_TOK_BYTES *= *\([0-9]*\);.*/\1/p' \
               $(REPO_ROOT)/gateware/src/src_hdl/canon_pkg.sv)
export TOK_BYTES

VERILOG_SOURCES  = $(REPO_ROOT)/gateware/src/src_hdl/eth_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/canon_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/recomp_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/norm_chk.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/log2_iter.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/recomp_feed.sv

include $(REPO_ROOT)/tb/common.mk
