# cocotb testbench for batch_buffer.sv
TOPLEVEL_LANG    = verilog
TOPLEVEL         = batch_buffer
MODULE           = test_batch_buffer

REPO_ROOT       := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
VERILOG_SOURCES  = $(REPO_ROOT)/gateware/src/src_hdl/eth_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/batch_buffer.sv

# Shrunk geometry so batches turn over quickly in simulation
# (must match the constants in test_batch_buffer.py).
COMPILE_ARGS    += -Pbatch_buffer.BANK_WORDS=64
COMPILE_ARGS    += -Pbatch_buffer.GRACE_PERIOD=16
COMPILE_ARGS    += -Pbatch_buffer.MAX_ENTRY_WORDS=17
# OUTPUT_SWAP is compile-time; `make BB_OUTPUT_SWAP=1` builds+exercises the
# D_SWAP trailing-delimiter path (buffer_rsp's config). The Python TB reads the
# same var so its swap-beat assertions match the build.
BB_OUTPUT_SWAP  ?= 0
export BB_OUTPUT_SWAP
COMPILE_ARGS    += -Pbatch_buffer.OUTPUT_SWAP=$(BB_OUTPUT_SWAP)

include $(REPO_ROOT)/tb/common.mk
