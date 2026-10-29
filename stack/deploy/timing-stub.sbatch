#!/bin/bash
# The timing stub: the receiving side of a timing test, on one pinned core. Put it where the interlock's port would be
# (submit with -w <node>). BUCKET_MS, PORT (default 17001). CPU only.
#SBATCH --job-name=ilstack-tstub
#SBATCH -p main
#SBATCH -w h100-sxm-8-0
#SBATCH --gpus=0
#SBATCH -c 2
#SBATCH --hint=nomultithread
#SBATCH --mem=2G
#SBATCH --time=00:10:00
#SBATCH -o /clusterhome/%u/ilstack/run/tstub-%j.out
cd ~/ilstack; . env/bin/activate
exec ilstack timing-stub --listen "0.0.0.0:${PORT:-17001}" --bucket-ms "${BUCKET_MS:-1}" --core auto
