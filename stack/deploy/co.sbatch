#!/bin/bash
# Compute on the 8-GPU node. ENGINE=mock|vllm, MODEL a HuggingFace name, TP the tensor-parallel size
# (pass --gpus=$TP to sbatch as well; --gpus=0 for a CPU-only run with the mock engine). The pacer takes a core of its own.
# IL_HOST is where the interlock listens (default: the frontend node).
#SBATCH --job-name=ilstack-co
#SBATCH -p main
#SBATCH -w h100-sxm-8-0
#SBATCH --gpus=1
#SBATCH -c 8
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH -o /clusterhome/%u/ilstack/run/co.out
cd ~/ilstack; . env/bin/activate
export VLLM_USE_FLASHINFER_SAMPLER=0      # the node has no nvcc for FlashInfer's JIT sampler
exec ilstack compute --keys keys --interlock "${IL_HOST:-h100-nvl-1-0-cc}:${PORT_CO:-7002}" --release process --core auto ${EXTRA:-} --bucket-ms "${BUCKET_MS:-10}" --bkt-num "${BKT_NUM:-1000}" \
  --engine "${ENGINE:-mock}" --model "${MODEL:-Qwen/Qwen2.5-0.5B-Instruct}" --tensor-parallel "${TP:-1}" --gpu-mem 0.3 \
  > run/compute.log 2>&1
