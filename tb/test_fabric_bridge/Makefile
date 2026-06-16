# cocotb testbench for fabric_bridge.sv
TOPLEVEL_LANG    = verilog
TOPLEVEL         = fabric_bridge
MODULE           = test_fabric_bridge

REPO_ROOT       := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
VERILOG_SOURCES  = $(REPO_ROOT)/gateware/src/src_hdl/eth_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/crc32_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/eth_deframe.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/eth_reframe.sv
# interlock core stack (tap spliced into fabric_bridge)
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
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/fabric_bridge.sv

include $(REPO_ROOT)/tb/common.mk
