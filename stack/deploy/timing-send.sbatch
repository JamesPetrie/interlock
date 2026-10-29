#!/bin/bash
# The timing sender: the pacer on a pinned core, fed with full buckets, the main process on another.
# STUB=host:port, BUCKET_MS, SLOTS, BUCKETS, TARGET, FILL, LINK_MBPS (the wire time packets are spaced at),
# EXTRA (further flags). Submit with -w <node> to choose the node. CPU only.
#SBATCH --job-name=ilstack-tsend
#SBATCH -p main
#SBATCH -w h100-nvl-1-0-cc
#SBATCH --gpus=0
#SBATCH -c 3
#SBATCH --hint=nomultithread
#SBATCH --mem=4G
#SBATCH --time=00:10:00
#SBATCH -o /clusterhome/%u/ilstack/run/tsend-%j.out
cd ~/ilstack; . env/bin/activate
export ILSTACK_LOWJITTER=${ILSTACK_LOWJITTER:-1}
exec ilstack timing-send --stub "${STUB:-h100-sxm-8-0:17001}" --bucket-ms "${BUCKET_MS:-1}" --slots "${SLOTS:-64}" \
  --buckets "${BUCKETS:-300000}" --target "${TARGET:-0.05}" --fill "${FILL:-0.95}" --link-mbps "${LINK_MBPS:-1000}" \
  --in-flight 200 --ahead 200 --release process --core auto ${EXTRA}
