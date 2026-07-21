#!/bin/bash
# Interactive multi-turn chat client on the interlock PORT 0 NIC.
#
#  - On a Mac: don't use this; run infcli directly (scapy needs root for /dev/bpf):
#       sudo python3 infcli.py --iface en7 --model ~/models/llama-2-7b-hf chat
#  - On the Spark loopback: run it in a container (no passwordless sudo on the host):
#       bash client_run.sh enxb8fbb3b1f53c
set -e
IFACE="${1:-enxb8fbb3b1f53c}"                     # interlock PORT 0 NIC
IMG="${ILK_IMG:-nvcr.io/nvidia/pytorch:25.11-py3}"
exec docker run --rm -it --name ilk_client --network host \
  --cap-add NET_RAW --cap-add NET_ADMIN \
  -v "${APP_DIR:-$HOME/fpe}":/app -v "${MODELS_DIR:-$HOME/models}":/models \
  "$IMG" bash -lc "pip install -q scapy transformers >/dev/null 2>&1; \
                   python -u /app/infcli.py --iface $IFACE --model /models/llama-2-7b-hf chat"
