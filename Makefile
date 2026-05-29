# Repo-level entry points. Heavy lifting lives in tb/ and gateware/.

.PHONY: test test-fabric-bridge clean-test gateware help

help:
	@echo "Targets:"
	@echo "  test               — run all cocotb unit tests"
	@echo "  test-fabric-bridge — run just the fabric_bridge cocotb tests"
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

clean-test:
	find tb -type d -name sim_build -exec rm -rf {} +
	find tb -type d -name __pycache__ -exec rm -rf {} +
	find tb -name 'results.xml' -delete
	find tb -name '*.vcd' -delete

gateware:
	./build.sh
