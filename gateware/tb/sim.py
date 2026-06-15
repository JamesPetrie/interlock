#!/usr/bin/env python3
"""Run a gateware unit/integration sim via cocotb + Icarus.

Usage:  python3 gateware/tb/sim.py <suite>
Golden references live in ../../prototype (wire.py) and Python hashlib, so every
suite checks the RTL against the same bytes the real system uses.
"""
import os
import sys
from pathlib import Path

GW = Path(__file__).resolve().parents[1]          # gateware/
SRC = GW / "src"
TB = GW / "tb"
SECW = SRC / "secworks"
CORE = SRC / "core"
PROTO = GW.parent / "prototype"

# test modules import `wire` from the prototype and each other from tb/
os.environ["PYTHONPATH"] = os.pathsep.join(
    [str(TB), str(PROTO), os.environ.get("PYTHONPATH", "")])

SECWORKS = [SECW / "sha256_k_constants.v", SECW / "sha256_w_mem.v", SECW / "sha256_core.v"]

SUITES = {
    # G0: the vendored SHA-256 core vs NIST vectors + fuzz against hashlib
    "sha256_core": dict(toplevel="sha256_core", module="test_sha256_core",
                        sources=SECWORKS),
    # G1: the streaming wrapper (byte-stream -> padded blocks -> digest)
    "sha256_stream": dict(toplevel="sha256_stream", module="test_sha256_stream",
                          sources=SECWORKS + [CORE / "sha256_stream.v"]),
    # G2: per-packet record path (H(ct) -> packet_hash -> record)
    "pkt_record": dict(toplevel="pkt_record", module="test_pkt_record",
                       sources=SECWORKS + [CORE / "sha256_stream.v", CORE / "pkt_record.v"]),
}


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in SUITES:
        sys.exit(f"usage: sim.py <{'|'.join(SUITES)}>")
    s = SUITES[sys.argv[1]]
    from cocotb_tools.runner import get_runner
    runner = get_runner("icarus")
    runner.build(sources=[str(p) for p in s["sources"]],
                 hdl_toplevel=s["toplevel"], always=True, build_args=["-g2012"],
                 timescale=("1ns", "1ps"),
                 build_dir=str(TB / "sim_build" / sys.argv[1]))
    runner.test(hdl_toplevel=s["toplevel"], test_module=s["module"],
                timescale=("1ns", "1ps"), test_dir=str(TB))


if __name__ == "__main__":
    main()
