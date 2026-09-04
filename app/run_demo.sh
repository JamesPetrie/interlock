#!/usr/bin/env bash
# Full interlock demo, end to end, with a timing report.
#
#   ./run_demo.sh                 # fast: subsampled spot check (default)
#   ./run_demo.sh sound           # full four-round proof, tq=10
#   ./run_demo.sh sound 80        # production soundness
#   ./run_demo.sh wire            # certified round trip only, no proof
#   PROMPT='Question: Who wrote Hamlet?\nAnswer:' ./run_demo.sh
#
# MODES -- the distinction matters if anyone is watching:
#   wire   certificates only; no proof is computed.
#   fast   subsampled: ONE (token,layer) of ~460 is proven, at tq=1. Shows the
#          protocol's shape and produces the real U, but a cheating prover passes
#          ~99.8% of the time. NOT a proof; do not present it as one.
#   sound  the real four-round protocol over the whole forward pass.
#
# WHY THIS RESTARTS THE BACKEND: the prover is selected by CHALLENGE_PY, read by
# model_backend.py, and the query count by CHALLENGE_TQ, baked into ilk_server's
# docker env at bringup. Neither is per-request -- setting them on the client
# does nothing and you silently get whatever mode the backend already had. So
# switching mode means restarting the backend, which costs ~20 s of model load.
# A marker file records the running mode so repeat runs skip the restart.
set -uo pipefail

MODE="${1:-fast}"
TQ_ARG="${2:-}"
# Both repos locate each other relatively: VerInf is expected as a sibling
# checkout of the interlock repo. Override either with the environment.
APP="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERINF="${VERINF:-$(cd "$APP/../.." && pwd)/VerInf}"
PY="$VERINF/venv/bin/python"
MARK=/tmp/interlock-demo-mode

case "$MODE" in
  wire)  CHAL=""; TQ="${TQ_ARG:-10}";;
  fast)  CHAL="$VERINF/analysis/subsample_challenge.py"; TQ="${TQ_ARG:-1}";;
  sound) CHAL="$VERINF/analysis/interlock_challenge.py";  TQ="${TQ_ARG:-10}";;
  *) echo "usage: $0 [wire|fast|sound] [t-queries]" >&2; exit 2;;
esac

hr() { printf '%.0s-' {1..66}; echo; }
now() { date +%s.%N; }
since() { awk -v a="$(now)" -v b="$1" 'BEGIN{printf "%.1f", a-b}'; }
port_up() { timeout 3 python3 -c "import socket;socket.create_connection(('127.0.0.1',9917),2)" 2>/dev/null; }

# run_demo drives the Pi over ssh with infcli, which opens the canonical port and
# runs its own flywheel. pi_agent (the web demo) holds that port for its whole
# life. Both at once puts two senders on the same wire, and two packets landing in
# one 1 ms bucket makes commit.bucket_hash raise -- the audit does not degrade, it
# throws. They are alternatives, never concurrent, so say so instead of producing
# a confusing failure deep in the run.
if timeout 10 ssh -o BatchMode=yes -o ConnectTimeout=6 2a-rpi \
       "pgrep -f '[p]i_agent\.py' >/dev/null" 2>/dev/null; then
  echo "  REFUSING: pi_agent is running on the Pi and holds the canonical port."
  echo "            run_demo.sh and the web demo are alternatives, not concurrent."
  echo "            Stop it first:  ./demo_up.sh stop"
  exit 1
fi

T0=$(now)
hr
echo "  INTERLOCK DEMO   mode=$MODE  tq=$TQ"
[ "$MODE" = fast ] && echo "  ** spot check, NOT a proof (1 token-layer of ~460, tq=1) **"
hr

# ---- backend in the right mode --------------------------------------------
S=$(now)
WANT="$MODE:$TQ:$(basename "${CHAL:-none}")"
HAVE=$(cat "$MARK" 2>/dev/null || echo "")
if [ "$HAVE" != "$WANT" ] || ! port_up; then
  echo "  backend   : restarting for mode=$MODE (was: ${HAVE:-none})"
  # Kill by explicit pid: bringup's own pgrep check matches any command line
  # containing the name, including the shell that invokes it.
  ps -eo pid,args | grep "venv/bin/python -u model_backend.py" \
    | grep -v "bash -c" | awk '{print $1}' | xargs -r kill -9 2>/dev/null
  for _ in $(seq 1 20); do port_up || break; sleep 1; done
  ( cd "$APP" && CHALLENGE_PY="${CHAL:+$PY -u $CHAL}" CHALLENGE_TQ="$TQ" \
      ./bringup.sh >/tmp/interlock-demo-bringup.log 2>&1 )
  if ! port_up; then
    echo "  FAIL  backend did not come up; see /tmp/interlock-demo-bringup.log"; exit 1
  fi
  echo "$WANT" > "$MARK"
else
  echo "  backend   : already in mode=$MODE"
fi

# NB: capture first, do not pipe. `grep -q` exits on the first match, docker
# logs then takes SIGPIPE, and `set -o pipefail` turns that into rc=141 -- a
# false "board is dead" that only appears once the log is long enough that
# docker is still writing when grep quits.
ILK_LOG=$(docker logs ilk_server 2>&1 || true)
if ! grep -qa "locked: bucket=" <<<"$ILK_LOG"; then
  echo "  FAIL  no bucket lock: the board is not emitting sync."
  echo "        Power-cycle the interlock (a reflash does not restart it);"
  echo "        it goes quiet ~4h after each power-up."
  exit 1
fi
T_PRE=$(since "$S")
MODEL=$(grep -a "listening on" /tmp/interlock-logs/backend.log | tail -1 | sed 's/.*model=//;s/)//')
echo "  model     : $(basename "$MODEL")"
echo "  preflight : board locked, backend up (${T_PRE}s)"
hr

# ---- run -------------------------------------------------------------------
S=$(now)
if [ "$MODE" = wire ]; then
  OUT=$("$PY" -u "$APP/demo_e2e.py" --no-challenge 2>&1) || true
else
  OUT=$("$PY" -u "$APP/demo_e2e.py" 2>&1) || true
fi
T_RUN=$(since "$S")

echo "$OUT" | grep -aE '^\[[0-9]\]|^  (OK|FAIL|n/a|!!)|^      [A-Za-z0-9"]|RESULT:|tau |unexplained information|proof verify|output-binding|H\(proof\)' \
  | sed 's/^/  /'
hr

# ---- timings ---------------------------------------------------------------
get() { echo "$OUT" | grep -aoP "$1" | head -1; }
CH=$(get 'challenge finished in \K[0-9]+')
DEMO=$(get '^total \K[0-9]+')
BA=$(grep -aoP 'phase A \(forward \+ capture\): \K[0-9.]+' /tmp/interlock-logs/backend.log | tail -1)
BB=$(grep -aoP 'phase B \(prove\): *\K[0-9.]+' /tmp/interlock-logs/backend.log | tail -1)
BC=$(grep -aoP 'phase C \(verify\): *\K[0-9.]+' /tmp/interlock-logs/backend.log | tail -1)

echo "  TIMINGS"
printf "    %-36s %6ss\n" "backend ready / preflight" "$T_PRE"
[ -n "${BA:-}" ] && printf "    %-36s %6ss\n" "  prover: forward + capture" "$BA"
[ -n "${BB:-}" ] && printf "    %-36s %6ss\n" "  prover: prove" "$BB"
[ -n "${BC:-}" ] && printf "    %-36s %6ss\n" "  prover: verify (Rust)" "$BC"
[ -n "${CH:-}" ] && printf "    %-36s %6ss\n" "challenge incl. wire round trips" "$CH"
[ -n "${DEMO:-}" ] && printf "    %-36s %6ss\n" "demo_e2e internal total" "$DEMO"
printf "    %-36s %6ss\n" "run (tokenize+send+decode+proof)" "$T_RUN"
printf "    %-36s %6ss\n" "TOTAL WALL (excl. backend restart)" \
       "$(awk -v a="$T_RUN" 'BEGIN{printf "%.1f", a}')"
printf "    %-36s %6ss\n" "TOTAL WALL (incl. restart)" "$(since "$T0")"
hr

if echo "$OUT" | grep -q "RESULT: PASS"; then
  echo "  RESULT: PASS  $(echo "$OUT" | grep -aoP 'mode=\S+ pick=\S+' | head -1)"
  # Label from what the prover ACTUALLY reported, never from the mode we asked
  # for. The two can disagree: bringup.sh started with a different CHALLENGE_PY
  # leaves the marker file claiming a mode the backend is not running, and the
  # footer would then describe the wrong run. Under-claiming is harmless;
  # over-claiming -- printing a full-proof footer over a spot check -- is not.
  # `mode=subsample` rides the raw CHALLENGE_RESULT line, which demo_e2e parses
  # into the panel rather than echoing, so it is not in $OUT. This used to sniff
  # the weld line instead -- the subsampled prover forced it to "n/a (spot-check
  # mode)" -- but that was a proxy for the mode, not the mode, and it broke the
  # moment the subsampled path learned to weld: a real spot check then printed a
  # full-proof footer, which is exactly the over-claim this block exists to stop.
  # Read the prover's own name out of the backend log instead. The worker logs
  # the script it warmed, so this says which prover ran rather than inferring it
  # from how the verdict happened to be worded.
  ACTUAL=$(grep -a "\[worker\] warm" /tmp/interlock-logs/backend.log 2>/dev/null \
           | tail -1 | grep -oE "[a-z_]+_challenge\.py")
  if [ "${ACTUAL:-}" = "subsample_challenge.py" ] \
     || { [ -z "${ACTUAL:-}" ] && echo "$OUT" | grep -qE "spot-check mode|sampled forward pass"; }; then
    echo "  (spot check -- not a proof)"
    [ "$MODE" = sound ] && echo "  !! asked for sound, but the backend ran the SUBSAMPLED prover."
  else
    [ "$MODE" = fast ] && echo "  !! asked for fast, but the backend ran the FULL prover (slower, stronger)."
  fi
  exit 0
elif [ "$MODE" = wire ] && echo "$OUT" | grep -q "done in"; then
  echo "  RESULT: certified round trip complete (no proof requested)"; exit 0
else
  echo "  RESULT: did not pass -- full output above"; exit 1
fi
