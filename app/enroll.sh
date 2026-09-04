#!/usr/bin/env bash
# One-time model enrolment for the subsampled demo.
#
# A subsampled proof commits ONE layer's weights, so its root_w is a per-layer
# value -- and a fresh one every run unless it comes from a saved commitment
# handle. Enrolment runs one proof per layer to mint those handles, then builds
# a Merkle tree over the per-layer roots so policy can pin a single MODEL_ROOT.
# After this, verify_proof's enrolled-root check is independent and a swapped
# model is rejected.
#
# Needs the wire and the orchestrator up (bringup.sh, then demo_up.sh start).
# Takes about one proof per layer; TinyLlama is 22, so ~5 minutes.
#
#   ./enroll.sh          # enrol if not already enrolled
#   ./enroll.sh --force  # re-enrol from scratch (new secret seeds, new MODEL_ROOT)
set -uo pipefail

APP="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERINF="${VERINF:-$(cd "$APP/../.." && pwd)/VerInf}"
PY="$VERINF/venv/bin/python"
ENROLL_DIR="${ENROLL_DIR:-$VERINF/.enroll}"
POLICY="$ENROLL_DIR/model_policy.json"
MODEL_DIR="${MODEL_DIR:-$VERINF/models/TinyLlama-1.1B-Chat-v1.0}"
PORT="${PORT:-8770}"
# The prover reads this to override the verifier's coin. It MUST NOT outlive this
# script: left behind, it pins the pick for the real demo and hands the prover the
# one thing the verifier is supposed to choose.
FORCE_FILE=/tmp/interlock-force-layer
trap 'rm -f "$FORCE_FILE"' EXIT INT TERM

if [ "${1:-}" = "--force" ]; then
    echo "== re-enrolling from scratch (previous handles and MODEL_ROOT discarded) =="
    rm -rf "$ENROLL_DIR"
elif [ -f "$POLICY" ]; then
    echo "   already enrolled: MODEL_ROOT $("$PY" -c \
        'import json,sys;print(json.load(open(sys.argv[1]))["model_root"][:16])' \
        "$POLICY" 2>/dev/null)... ($POLICY)"
    exit 0
fi

N_LAYERS=$("$PY" -c 'import json,sys;print(json.load(open(sys.argv[1]+"/config.json"))["num_hidden_layers"])' \
           "$MODEL_DIR" 2>/dev/null)
if [ -z "${N_LAYERS:-}" ]; then
    echo "   FAIL cannot read num_hidden_layers from $MODEL_DIR/config.json" >&2
    exit 1
fi
mkdir -p "$ENROLL_DIR"
echo "== enrolling $N_LAYERS layers (one proof each; the wire must be up) =="

run_one() {   # $1 = layer index
    echo "$1" > "$FORCE_FILE"
    "$PY" - "$PORT" <<'PYEOF'
import socketio, sys, threading
done, ok = threading.Event(), {"v": None}
sio = socketio.Client()
@sio.on('beat:verdict', namespace='/demo')
def _v(d): ok["v"] = (d.get('result') or {}).get('verdict'); done.set()
@sio.on('beat:error', namespace='/demo')
def _e(d): ok["v"] = "ERROR: %s" % d.get('error'); done.set()
sio.connect('http://127.0.0.1:%s' % sys.argv[1], namespaces=['/demo'],
            transports=['websocket'])
sio.emit('demo:reset', namespace='/demo')
sio.sleep(0.4)
sio.emit('demo:run', {}, namespace='/demo')
got = done.wait(240)
sio.disconnect()
sys.exit(0 if got and ok["v"] == "PASS" else 1)
PYEOF
}

fails=0
for L in $(seq 0 $((N_LAYERS - 1))); do
    printf "   L%-2d " "$L"
    if run_one "$L" >/dev/null 2>&1; then
        echo "enrolled"
    else
        echo "FAILED -- is the wire up? (bringup.sh, demo_up.sh start)"
        fails=$((fails + 1))
    fi
done
rm -f "$FORCE_FILE"

if [ "$fails" -gt 0 ]; then
    echo "   $fails/$N_LAYERS layers did not enrol; not building a partial tree" >&2
    exit 1
fi

echo "== building the enrolment tree =="
"$PY" "$VERINF/analysis/enroll_tree.py" "$ENROLL_DIR" || exit 1
echo "   done -- every proof from here pins its weight root to MODEL_ROOT"
