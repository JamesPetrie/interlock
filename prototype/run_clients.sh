#!/bin/bash
# Client-side nodes only (interlock + verifier + frontend), for the FPGA topology
# (compute runs on the host). Point at the host:  COMPUTE_HOST=10.10.10.2 ./run_clients.sh
cd "$(dirname "$0")"
PY="${PY:-python3}"
$PY interlock.py & I=$!
$PY verifier.py  & V=$!
trap "kill $I $V 2>/dev/null" EXIT
echo "started: interlock($I) verifier($V); compute at ${COMPUTE_HOST:-127.0.0.1}"
$PY frontend.py
