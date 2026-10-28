#!/bin/bash
# The software interlock, on the confidential VM next to the frontend. CPU only.
# BUCKET_MS and BKT_NUM must match the frontend's and compute's.
#SBATCH --job-name=ilstack-il
#SBATCH -p main
#SBATCH -w h100-nvl-1-0-cc
#SBATCH -c 2
#SBATCH --mem=4G
#SBATCH --time=04:00:00
#SBATCH -o /clusterhome/%u/ilstack/run/il.out
cd ~/ilstack; . env/bin/activate
exec ilstack interlock --keys keys --bucket-ms "${BUCKET_MS:-10}" --bkt-num "${BKT_NUM:-1000}" \
  --listen-frontend 0.0.0.0:7001 --listen-compute 0.0.0.0:7002 > run/interlock.log 2>&1
