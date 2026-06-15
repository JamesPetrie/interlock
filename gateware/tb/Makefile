# cocotb suites are driven by the Python runner sim.py (Icarus). Run by name:
#   make interlock_core   make sha256_stream   make pkt_record   make sha256_core
# (each is equivalent to: python3 sim.py <suite>)
SUITES = sha256_core sha256_stream pkt_record interlock_core

.PHONY: $(SUITES) all clean
$(SUITES):
	python3 sim.py $@
all: $(SUITES)
clean:
	rm -rf sim_build __pycache__ results.xml
