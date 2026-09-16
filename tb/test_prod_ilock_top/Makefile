# cocotb end-to-end testbench for prod_ilock_top.sv (both directions + certs)
TOPLEVEL_LANG    = verilog
TOPLEVEL         = prod_ilock_top
MODULE           = test_prod_ilock_top

REPO_ROOT       := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
# Packages first, in dependency order (canon_pkg references eth_pkg).
VERILOG_SOURCES  = $(REPO_ROOT)/gateware/src/src_hdl/eth_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/canon_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/crc32_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/crypto/sha256_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/crypto/sha256_core.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/crypto/sha256_msg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/crypto/hmac_sha256.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/eth_deframe.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/canon_proc.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/batch_buffer.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/traffic_commit.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/prod_ilock_pipeline.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/cert_merge.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/eth_reframe.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/prod_ilock_top.sv

# Shrunk batch geometry so batches turn over quickly in simulation
# (must match the constants in test_prod_ilock_top.py). BATCH_CYCLES must
# leave room to *drain* a full bank through traffic_commit's hash engine
# (~1 byte/cycle vs the 4 byte/cycle drain), or the tick preempts the drain.
COMPILE_ARGS    += -Pprod_ilock_top.BANK_WORDS=1024
COMPILE_ARGS    += -Pprod_ilock_top.BATCH_CYCLES=10000
COMPILE_ARGS    += -Pprod_ilock_top.GUARD_CYCLES=600
COMPILE_ARGS    += -Pprod_ilock_top.MAX_ENTRY_WORDS=376

include $(REPO_ROOT)/tb/common.mk
