# Repo-level entry points. Heavy lifting lives in tb/ and gateware/.

.PHONY: test test-fabric-bridge test-vpk180 lint lint-vpk180 clean-test gateware help

help:
	@echo "Targets:"
	@echo "  test               — run all cocotb unit tests"
	@echo "  test-fabric-bridge — run just the fabric_bridge cocotb tests"
	@echo "  test-vpk180        — VPK180 shim benches (W=64 default; W=128|384)"
	@echo "  lint-vpk180        — Verilator lint of the VPK180 PL top (both cores)"
	@echo "  lint               — Verilator lint (-Wall) on the interlock RTL"
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

# Static lint over the interlock RTL. fabric_bridge is the current top; the
# source list lives in a Verilator command file so it isn't duplicated here.
VERILATOR    ?= verilator
LINT_TOP     := fabric_bridge
LINT_FILES   := gateware/src/src_hdl/interlock.vc

lint:
	$(VERILATOR) --lint-only -Wall -Wno-PINCONNECTEMPTY --top-module $(LINT_TOP) -F $(LINT_FILES)

# ---- VPK180 port (gateware_vpk180/) ----------------------------------------
W ?= 64
VPK180_TESTS := test_axis_downsize test_axis_upsize test_axis_pkt_fifo test_axis2tse test_tse2axis test_mac_port_shim

test-vpk180: .venv
	@for t in $(VPK180_TESTS); do \
	  echo "=== $$t (W=$(W)) ==="; \
	  PATH=$(VENV_BIN):$$PATH $(MAKE) -C tb/$$t SIM=$(SIM) W=$(W) DEPTH=64 || exit 1; \
	done

# ilock_pl with either core. -Wno-fatal: the shared core RTL carries a few
# pre-existing width/pin warnings that the PolarFire flow tolerates; they are
# reported, errors still fail.
lint-vpk180:
	@for k in 0 1; do \
	  $(VERILATOR) --lint-only -Wall -Wno-fatal -Wno-PINCONNECTEMPTY -Wno-DECLFILENAME -Wno-UNUSEDSIGNAL -Wno-UNUSEDPARAM \
	    -DSIM_NO_XPM -GMAC_AXIS_W=$(W) -GTOP_KIND=$$k --top-module ilock_pl \
	    gateware_vpk180/hdl/*.sv -F gateware_vpk180/core_sources.vc || exit 1; \
	done

clean-test:
	find tb -type d -name 'sim_build*' -exec rm -rf {} +
	find tb -type d -name __pycache__ -exec rm -rf {} +
	find tb -name 'results.xml' -delete
	find tb -name '*.vcd' -delete

gateware:
	./build.sh
