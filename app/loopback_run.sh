#!/bin/bash
# Non-interactive loopback validation (PORT 0 client) in a container — drives one prompt
# through the interlock and runs the in-band ZK challenge. Pair with server_run.sh.
#   bash loopback_run.sh enxb8fbb3b1f53c
#   EXTRA='--prompt "Question: Name three primes.\nAnswer:"' bash loopback_run.sh
set -e
IFACE="${1:-enxb8fbb3b1f53c}"                     # interlock PORT 0 NIC
IMG="${ILK_IMG:-nvcr.io/nvidia/pytorch:25.11-py3}"
docker run --rm --name ilk_client --network host \
  --cap-add NET_RAW --cap-add NET_ADMIN \
  -v "${APP_DIR:-$HOME/fpe}":/app -v "${MODELS_DIR:-$HOME/models}":/models \
  "$IMG" bash -lc "pip install -q scapy transformers >/dev/null 2>&1; \
                   python -u /app/loopback_demo.py --iface $IFACE --model /models/llama-2-7b-hf ${EXTRA:-}"
