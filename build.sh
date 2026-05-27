#!/bin/bash
# One-shot build wrapper for the interlock gateware.
# Runs Libero against gateware/script.tcl, logs to /tmp, sha256s the .job.
#
# Usage:
#   ./build.sh            # build only
#   ./build.sh --scp DST  # scp the .job to DST after build (e.g., laptop:~/Desktop/)
set -eo pipefail

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
echo "=== starting build at $(date -Iseconds) ==="
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
echo "=== build done in ${ELAPSED}s ==="
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
