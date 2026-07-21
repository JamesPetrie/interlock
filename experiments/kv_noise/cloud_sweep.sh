#!/usr/bin/env bash
# cloud_sweep.sh — turnkey KV-noise collection cell for a rented cloud GPU.
#
# Paste/rsync this directory onto a fresh pod (RunPod, Lambda, ...) and run:
#
#     ./cloud_sweep.sh                 # 2 back-to-back bf16 sdpa runs
#     ./cloud_sweep.sh --det           # ... plus a determinism-flags run
#     ./cloud_sweep.sh --attn eager
#     ./cloud_sweep.sh --dtype float16
#     ./cloud_sweep.sh --full          # also tar the full tensor dumps
#
# It auto-derives a descriptive run-id base from the environment
# ({gpu}-t{torchver}-{attn}[-fp16]-{podid}), runs the collection protocol,
# prints the on-pod analysis summary (so you see the H1 verdict before
# tearing the pod down), and tars up the manifests for download.
#
# The pod-id component keeps run-ids unique when the same command runs on
# two same-SKU pods simultaneously (the H3 experiment) — the analyzer
# compares them as one stack, which is exactly the H3 test.
set -euo pipefail
cd "$(dirname "$0")"

ATTN=sdpa
DTYPE=bfloat16
RUNS=2
DET=0
FULL=0
TAG=""
while [ $# -gt 0 ]; do
    case "$1" in
        --attn)  ATTN=$2; shift 2 ;;
        --dtype) DTYPE=$2; shift 2 ;;
        --runs)  RUNS=$2; shift 2 ;;
        --tag)   TAG=$2; shift 2 ;;
        --det)   DET=1; shift ;;
        --full)  FULL=1; shift ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

command -v nvidia-smi >/dev/null || { echo "no nvidia-smi — is this a GPU pod?" >&2; exit 1; }
GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)

# "NVIDIA A100-SXM4-80GB" -> a100, "NVIDIA GeForce RTX 4090" -> rtx4090,
# "NVIDIA L40S" -> l40s. Override with GPU_SLUG=... if the heuristic misfires.
slug=$(echo "$GPU_NAME" | tr '[:upper:]' '[:lower:]' \
       | sed -E 's/nvidia|geforce|tesla//g' | xargs | sed -E 's/^rtx +/rtx/')
slug=${slug%% *}; slug=${slug%%-*}
GPU_SLUG=${GPU_SLUG:-$slug}

# Use the image's python if it already has torch (RunPod/Lambda pytorch
# images do — that IS the stack under test); otherwise build a venv.
PY=python3
if ! $PY -c 'import torch' 2>/dev/null; then
    echo "no torch in system python — creating venv and installing"
    $PY -m venv .venv && PY=.venv/bin/python
    $PY -m pip -q install torch
fi
$PY -c 'import transformers, safetensors' 2>/dev/null \
    || $PY -m pip -q install transformers safetensors

if [ "$ATTN" = flash_attention_2 ] && ! $PY -c 'import flash_attn' 2>/dev/null; then
    echo "attn=flash_attention_2 needs flash-attn installed first, e.g.:" >&2
    echo "    $PY -m pip install flash-attn --no-build-isolation" >&2
    exit 1
fi

TORCH_TAG=$($PY -c 'import torch; print("t"+torch.__version__.split("+")[0].replace(".",""))')
POD_ID=$(echo "${RUNPOD_POD_ID:-$(hostname)}" | tr -dc '[:alnum:]' \
         | tr '[:upper:]' '[:lower:]' | tail -c 5)
BASE="${GPU_SLUG}-${TORCH_TAG}-${ATTN}"
[ "$DTYPE" = float16 ] && BASE="${BASE}-fp16"
[ -n "$TAG" ] && BASE="${BASE}-${TAG}"
BASE="${BASE}-${POD_ID}"

echo "== GPU: $GPU_NAME  ->  run-id base: $BASE"

for n in $(seq 1 "$RUNS"); do
    $PY collect_kv_dumps.py --run-id "${BASE}-${n}" \
        --dtype "$DTYPE" --attn-implementation "$ATTN"
done
if [ "$DET" = 1 ]; then
    $PY collect_kv_dumps.py --run-id "${BASE}-det-1" \
        --dtype "$DTYPE" --attn-implementation "$ATTN" --deterministic
fi

echo; echo "== on-pod analysis (H1: are back-to-back runs bit-identical?)"
$PY analyze_runs.py kv_noise_data/

# Manifests are all the hash-level claims need (KBs); tensors only matter
# for cross-stack numeric diffs, so --full is opt-in (GBs).
MANIFEST_TAR="manifests_${BASE}.tar.gz"
( cd kv_noise_data && \
  find run_"${BASE}"-* \( -name 'manifest.jsonl' -o -name 'meta.json' \
       -o -name 'prompts.json' \) -print0 \
  | tar -czf "../$MANIFEST_TAR" --null -T - )
echo "== manifests: $(pwd)/$MANIFEST_TAR ($(du -h "$MANIFEST_TAR" | cut -f1))"

if [ "$FULL" = 1 ]; then
    FULL_TAR="dumps_${BASE}.tar.gz"
    ( cd kv_noise_data && tar -czf "../$FULL_TAR" run_"${BASE}"-* )
    echo "== full dumps: $(pwd)/$FULL_TAR ($(du -h "$FULL_TAR" | cut -f1))"
fi

echo
echo "pull results with (RunPod: port/IP from the pod's Connect panel):"
echo "    scp -P <port> root@<ip>:$(pwd)/${MANIFEST_TAR} ."
