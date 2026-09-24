# Shared cocotb / Verilator settings included by per-test Makefiles.
# Each test Makefile sets TOPLEVEL, MODULE, VERILOG_SOURCES, then includes this.

# Resolve repo root (one up from tb/)
REPO_ROOT ?= $(realpath $(dir $(lastword $(MAKEFILE_LIST)))/..)

# Use the repo's virtualenv for Python (cocotb, scapy). Prepending venv/bin
# to PATH makes `python3` and cocotb's helper scripts resolve to the venv
# without explicit invocations.
VENV     := $(REPO_ROOT)/.venv
export PATH := $(VENV)/bin:$(PATH)
PYTHON   := $(VENV)/bin/python
COCOTB   := $(VENV)/bin/cocotb-config

# Default simulator: icarus (apt verilator 5.020 is older than cocotb 2.x needs)
SIM      ?= icarus
WAVES    ?= 0

# Simulator-specific options
ifeq ($(SIM),verilator)
    EXTRA_ARGS += --trace --trace-structs
endif

# Build directory inside the per-test dir (gitignored)
SIM_BUILD ?= sim_build

# Path that lets cocotb find Python modules in the test dir
export PYTHONPATH := $(PWD):$(PYTHONPATH)

# cocotb's stock Makefile drives the simulation; include it last
include $(shell $(COCOTB) --makefiles)/Makefile.sim
