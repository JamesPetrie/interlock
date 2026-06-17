# cocotb: host-style folding through the FULL chain — ethernet frame carrying a
# wire packet -> eth_deframe -> interlock_tap -> interlock_core -> cert. Verifies
# a correctly-bucketed packet FOLDS into a non-empty cert (byte-exact vs the Python
# golden) and a wrong-bucket packet is DROPPED (cert stays empty). This is the path
# the host exercises (which the cert-egress test never checked for folding).
# Large TICK_DIV so the frame lands in bucket 0 before the first tick.
TOPLEVEL_LANG    = verilog
TOPLEVEL         = fabric_bridge
MODULE           = test_fold_frames

REPO_ROOT       := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
VERILOG_SOURCES  = $(REPO_ROOT)/gateware/src/src_hdl/eth_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/crc32_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/eth_deframe.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/eth_reframe.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/secworks/sha256_k_constants.v
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/secworks/sha256_w_mem.v
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/secworks/sha256_core.v
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/core/sha256_stream.v
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/core/pkt_record.v
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/core/hmac_sha256.v
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/core/interlock_core.v
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/core/axis32_to_bytes.v
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/core/interlock_tap.v
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/core/bytes2axis32.v
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/core/cert_framer.v
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/core/mac_tx_mux.v
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/core/tick_beacon.v
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/core/cert_fifo.v
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/fabric_bridge.sv

# big tick window so the injected frame folds into bucket 0 before tick 1
COMPILE_ARGS += -Pfabric_bridge.TICK_DIV=4000

include $(REPO_ROOT)/tb/common.mk
