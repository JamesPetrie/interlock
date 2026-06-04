# cocotb testbench for fabric_bridge.sv
TOPLEVEL_LANG    = verilog
TOPLEVEL         = fabric_bridge
MODULE           = test_fabric_bridge

REPO_ROOT       := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
# TODO: port this TB to Verilator (default SIM=icarus today) and consume the
# shared gateware/src/src_hdl/interlock.vc filelist via Verilator's -F instead
# of duplicating the list below. Blocked on: the deploy box's apt Verilator
# (5.020) is too old for cocotb 2.x, and the RTL width warnings must be fixed
# first (Verilator's compile is fatal on them). `make lint` already uses the .vc.
VERILOG_SOURCES  = $(REPO_ROOT)/gateware/src/src_hdl/eth_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/crc32_pkg.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/eth_deframe.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/eth_reframe.sv
VERILOG_SOURCES += $(REPO_ROOT)/gateware/src/src_hdl/fabric_bridge.sv

include $(REPO_ROOT)/tb/common.mk
