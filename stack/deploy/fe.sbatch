#!/bin/bash
# The frontend on the confidential VM. TOKENIZER=mock or a HuggingFace name; MODEL_NAME is what /v1/models reports.
# INGRESS=user@host is the ingress box; the job opens the reverse tunnel to it (INGRESS= to run without one).
# The pacer process takes a core of its own. IL_HOST is where the interlock listens (default: this node).
# The prover log is fresh per job (LOG_ID overrides): a restarted software interlock numbers its buckets from zero again.
#SBATCH --job-name=ilstack-fe
#SBATCH -p main
#SBATCH -w h100-nvl-1-0-cc
#SBATCH -c 4
#SBATCH --mem=16G
#SBATCH --time=04:00:00
#SBATCH -o /clusterhome/%u/ilstack/run/fe.out
cd ~/ilstack; . env/bin/activate
INGRESS="${INGRESS-iltunnel@178.104.148.144}"
if [ -n "$INGRESS" ]; then
  stack/deploy/tunnel.sh "$INGRESS" 8000 127.0.0.1:8000 > run/tunnel.log 2>&1 &
fi
exec ilstack frontend --keys keys --interlock "${IL_HOST:-127.0.0.1}:${PORT_FE:-7001}" --release process --core auto ${EXTRA:-} --bucket-ms "${BUCKET_MS:-10}" --bkt-num "${BKT_NUM:-1000}" \
  --tokenizer "${TOKENIZER:-mock}" --model-name "${MODEL_NAME:-mock}" --http 0.0.0.0:8000 --log-dir "run/prover-log-${LOG_ID:-$SLURM_JOB_ID}" \
  > run/frontend.log 2>&1
