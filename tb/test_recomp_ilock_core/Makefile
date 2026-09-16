# cocotb testbench for recomp_ilock_core.sv — MAC-level end-to-end
TOPLEVEL_LANG    = verilog
TOPLEVEL         = recomp_ilock_core
MODULE           = test_recomp_ilock_core,test_recomp_gate

REPO_ROOT       := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))/../..)

# tell the TB the token width (env override wins, e.g. for width sweeps)
TOK_BYTES ?= $(shell sed -n 's/.*CANON_TOK_BYTES *= *\([0-9]*\);.*/\1/p' \
               $(REPO_ROOT)/gateware/src/src_hdl/canon_pkg.sv)
export TOK_BYTES

# shrink the bucket period and cert cadence for simulation
COMPILE_ARGS += -Precomp_ilock_core.TIMER_END=4999
COMPILE_ARGS += -Precomp_ilock_core.BKTS_PER_CERT=1

H = $(REPO_ROOT)/gateware/src/src_hdl
VERILOG_SOURCES  = $(H)/eth_pkg.sv $(H)/crc32_pkg.sv $(H)/canon_pkg.sv $(H)/recomp_pkg.sv
VERILOG_SOURCES += $(H)/crypto/sha256_pkg.sv $(H)/crypto/sha256_core.sv
VERILOG_SOURCES += $(H)/crypto/sha256_msg.sv $(H)/crypto/hmac_sha256.sv
VERILOG_SOURCES += $(H)/eth_deframe.sv $(H)/eth_reframe.sv $(H)/canon_proc.sv
VERILOG_SOURCES += $(H)/axis_pkt_gate.sv $(H)/axis_mux3.sv $(H)/axis_splitter.sv
VERILOG_SOURCES += $(H)/batch_buffer.sv $(H)/traffic_commit.sv $(H)/record_layer.sv
VERILOG_SOURCES += $(H)/serializer.sv $(H)/leaf_hash.sv $(H)/sticky_bit.sv
VERILOG_SOURCES += $(H)/cert_build.sv $(H)/norm_chk.sv $(H)/log2_iter.sv
VERILOG_SOURCES += $(H)/recomp_feed.sv $(H)/recomp_ilock_core.sv

# reuse the recomp_feed TB's estimate builders and exact U model
export PYTHONPATH := $(PWD):$(REPO_ROOT)/tb/test_recomp_feed:$(PYTHONPATH)

include $(REPO_ROOT)/tb/common.mk
