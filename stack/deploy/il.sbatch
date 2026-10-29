#!/bin/bash
# The software interlock, on the confidential VM next to the frontend (sbatch -w <node> --gpus=0 to put it elsewhere).
# CPU only; busy-polls on a core of its own.
# BUCKET_MS and BKT_NUM must match the frontend's and compute's; PORT_FE and PORT_CO are the two listening ports.
#SBATCH --job-name=ilstack-il
#SBATCH -p main
#SBATCH -w h100-nvl-1-0-cc
#SBATCH -c 3
#SBATCH --mem=4G
#SBATCH --time=04:00:00
#SBATCH -o /clusterhome/%u/ilstack/run/il.out
cd ~/ilstack; . env/bin/activate
# a trace, if asked for, goes to local disk (a write to the shared home stalls the loop) and is copied back on exit
if [ -n "$ILSTACK_TRACE" ]; then export ILSTACK_TRACE=/tmp/ilstack-trace-$SLURM_JOB_ID; trap "cp $ILSTACK_TRACE run/interlock-trace.txt" EXIT; fi
ilstack interlock --keys keys --spin --core auto --bucket-ms "${BUCKET_MS:-10}" --bkt-num "${BKT_NUM:-1000}" \
  --listen-frontend "0.0.0.0:${PORT_FE:-7001}" --listen-compute "0.0.0.0:${PORT_CO:-7002}" > run/interlock.log 2>&1
