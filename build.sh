#!/bin/bash
# One-shot build wrapper for the interlock gateware.
# Runs Libero against gateware/script.tcl, logs to /tmp, sha256s the .job.
#
# Usage:
#   ./build.sh                          # recomp top, 100 ms buckets (defaults)
#   TOP=prod BUCKET_MS=1 ./build.sh     # prod top, production 1 ms buckets
#   ./build.sh --scp DST                # scp the .job to DST after build
set -eo pipefail

TOP="${TOP:-recomp}"
BUCKET_MS="${BUCKET_MS:-100}"
case "$TOP" in prod|recomp) ;; *) echo "TOP must be prod or recomp" >&2; exit 1 ;; esac
case "$BUCKET_MS" in 1|100) ;; *) echo "BUCKET_MS must be 1 or 100" >&2; exit 1 ;; esac
export TOP BUCKET_MS

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
GATEWARE_DIR="$REPO_ROOT/gateware"
LOG="/tmp/build_$(date +%Y%m%d_%H%M%S).log"
JOB_PATH="$GATEWARE_DIR/Libero_Project/designer/top/export/top.job"

# Parse args
SCP_DEST=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --scp) SCP_DEST="$2"; shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 1 ;;
    esac
done

# Wipe stale build dir
echo "=== wiping stale Libero_Project ==="
rm -rf "$GATEWARE_DIR/Libero_Project"

# Run Libero
echo "=== starting build at $(date -Iseconds) (TOP=$TOP BUCKET_MS=$BUCKET_MS) ==="
echo "log: $LOG"
cd "$GATEWARE_DIR"
SECONDS=0
if ! libero "SCRIPT:script.tcl" 2>&1 | tee "$LOG" 2>&1; then
    echo "BUILD FAILED — see $LOG"
    exit 1
fi
ELAPSED=$SECONDS

# Confirm .job exists
if [[ ! -f "$JOB_PATH" ]]; then
    echo "BUILD SUCCEEDED but .job missing at $JOB_PATH — log: $LOG"
    exit 1
fi

# Summary
SIZE=$(stat -c%s "$JOB_PATH")
DIGEST=$(sha256sum "$JOB_PATH" | awk '{print $1}')
echo
echo "=== build done in ${ELAPSED}s (TOP=$TOP BUCKET_MS=$BUCKET_MS) ==="
echo "  .job:    $JOB_PATH"
echo "  size:    $SIZE bytes"
echo "  sha256:  $DIGEST"
echo "  log:     $LOG"

# Optional scp
if [[ -n "$SCP_DEST" ]]; then
    echo
    echo "=== copying to $SCP_DEST ==="
    scp "$JOB_PATH" "$SCP_DEST"
fi
