# Repo-level entry points. Heavy lifting lives in tb/ and gateware/.

.PHONY: test test-fabric-bridge compile lint clean-test gateware help

help:
	@echo "Targets:"
	@echo "  test               — run all cocotb unit tests"
	@echo "  test-fabric-bridge — run just the fabric_bridge cocotb tests"
	@echo "  compile            — Verilator compile check (errors only) of one design"
	@echo "  lint               — Verilator lint (-Wall, warnings fatal) of one design"
	@echo "                       DESIGN=workload|recomputation (default: workload)"
	@echo "  clean-test         — remove cocotb sim_build dirs"
	@echo "  gateware           — run ./build.sh (Libero synth, ~22 min)"

# Default Python venv for tests
PYTHON ?= .venv/bin/python

.venv:
	python3 -m venv .venv
	.venv/bin/pip install --quiet --upgrade pip
	.venv/bin/pip install --quiet cocotb cocotb-bus scapy

SIM ?= icarus

test: test-fabric-bridge

VENV_BIN := $(realpath .)/.venv/bin

test-fabric-bridge: .venv
	PATH=$(VENV_BIN):$$PATH $(MAKE) -C tb/test_fabric_bridge SIM=$(SIM)

# Static checks over the interlock RTL, both run with Verilator on the design
# selected with DESIGN=<design>:
#   compile — elaborate the design; only errors fail (warnings are printed)
#   lint    — as compile plus the style warnings (-Wall); any warning fails
# Each design has a top module (TOP_<design>) and a Verilator command file
# ($(RTL_DIR)/<design>.vc) listing its sources; the command files pull in
# shared.vc for the sources common to all designs.
VERILATOR         ?= verilator
RTL_DIR           := gateware/src/src_hdl
DESIGN            ?= workload
TOP_workload      := fabric_bridge
TOP_recomputation := recomp_ilock_core
ifeq ($(TOP_$(DESIGN)),)
$(error Unknown DESIGN '$(DESIGN)': no TOP_$(DESIGN) is defined)
endif
# TODO: lint is not clean yet, so compile makes warnings non-fatal (-Wno-fatal)
# and CI runs only compile. Once lint is clean, drop -Wno-fatal and add lint
# to CI as well.
COMPILE_FLAGS     := --lint-only -Wno-fatal
LINT_FLAGS        := --lint-only -Wall -Wno-PINCONNECTEMPTY

compile:
	$(VERILATOR) $(COMPILE_FLAGS) --top-module $(TOP_$(DESIGN)) -F $(RTL_DIR)/$(DESIGN).vc

lint:
	$(VERILATOR) $(LINT_FLAGS) --top-module $(TOP_$(DESIGN)) -F $(RTL_DIR)/$(DESIGN).vc

clean-test:
	find tb -type d -name sim_build -exec rm -rf {} +
	find tb -type d -name __pycache__ -exec rm -rf {} +
	find tb -name 'results.xml' -delete
	find tb -name '*.vcd' -delete

gateware:
	./build.sh
