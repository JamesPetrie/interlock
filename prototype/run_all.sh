#!/bin/bash
# Launch the three server nodes, then the frontend (interactive).
#   MOCK=1 ./run_all.sh   # no-Llama wiring test (canned compute)
#   ./run_all.sh          # real Llama-2-7B, all four nodes on this host
cd "$(dirname "$0")"
PY="${PY:-python3}"
$PY compute.py   & C=$!
$PY interlock.py & I=$!
$PY verifier.py  & V=$!
trap "kill $C $I $V 2>/dev/null" EXIT
echo "started: compute($C) interlock($I) verifier($V) — frontend retries until they're up"
$PY frontend.py
