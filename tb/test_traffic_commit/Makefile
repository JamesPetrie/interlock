# cocotb testbench for traffic_commit.sv
TOPLEVEL_LANG    = verilog
TOPLEVEL         = traffic_commit
MODULE           = test_traffic_commit

REPO_ROOT       := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
# eth_pkg + canon_pkg: traffic_commit reads canon_pkg::CANON_RSP_HDR_BYTES for
# the reserved cert-header length (no import).
VERILOG_SOURCES  = $(REPO_ROOT)/gateware/src/src_hdl/eth_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/canon_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/crypto/sha256_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/crypto/sha256_core.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/crypto/sha256_msg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/crypto/hmac_sha256.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/axis_splitter.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/serializer.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/payload_hash.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/record_layer.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/cert_build.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/traffic_commit.sv

# Shrink the certificate period (buckets/second) so the TB emits certificates
# after a handful of batches; the Python reference reads the same BUCKETS_PER_SEC.
COMPILE_ARGS += -Ptraffic_commit.BUCKETS_PER_SEC=3

include $(REPO_ROOT)/tb/common.mk
