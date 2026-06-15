# cocotb testbench for interlock_tap.v (AXIS-32 -> core -> cert)
TOPLEVEL_LANG    = verilog
TOPLEVEL         = interlock_tap
MODULE           = test_interlock_tap

REPO_ROOT       := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
VERILOG_SOURCES  = $(REPO_ROOT)/gateware/src/secworks/sha256_k_constants.v
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/secworks/sha256_w_mem.v
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/secworks/sha256_core.v
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/core/sha256_stream.v
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/core/pkt_record.v
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/core/hmac_sha256.v
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/core/interlock_core.v
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/core/axis32_to_bytes.v
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/core/interlock_tap.v

include $(REPO_ROOT)/tb/common.mk
