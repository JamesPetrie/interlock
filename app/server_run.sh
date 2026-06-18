#!/bin/bash
# Launch the interlock model-server + in-band ZK handler in ONE GPU container (the Spark).
# Greedy generate, the ZK prove, and the Rust verify all run in here; the proof never
# crosses the wire (verify-on-Spark). Needs: --gpus (model + prover), CAP_NET_RAW +
# CAP_NET_ADMIN (raw L2 + promiscuous), --network host (the NIC lives on the host).
#
#   bash server_run.sh                 # PORT 1 NIC = enP7s7, sound proof (T=80)
#   MAX_NEW_TOKENS=96 CHALLENGE_TQ=80 bash server_run.sh enP7s7
#   CHALLENGE_TQ=4 bash server_run.sh  # fast/dev proof while iterating
set -e
IFACE="${1:-enP7s7}"                              # interlock PORT 1 NIC
IMG="${ILK_IMG:-nvcr.io/nvidia/pytorch:25.11-py3}"
INFPROOF="${INFPROOF_DIR:-$HOME/infproof}"
MODELS="${MODELS_DIR:-$HOME/models}"
APP="${APP_DIR:-$HOME/fpe}"
exec docker run --rm --name ilk_server --gpus all --ipc=host --network host \
  --cap-add NET_RAW --cap-add NET_ADMIN \
  -e MODEL_DIR=/models/llama-2-7b-hf \
  -e PRELOAD_MODEL=1 \
  -e MAX_NEW_TOKENS="${MAX_NEW_TOKENS:-64}" \
  -e STOP="${STOP:-\\nQuestion:}" \
  -e CHALLENGE_TQ="${CHALLENGE_TQ:-80}" \
  -e CHALLENGE_PY="python -u /infproof/analysis/interlock_challenge.py" \
  -v "$INFPROOF":/infproof -v "$MODELS":/models -v "$APP":/app \
  "$IMG" bash -lc "pip install -q blake3 transformers safetensors >/dev/null 2>&1; \
                   python -u /app/model_server.py $IFACE"
