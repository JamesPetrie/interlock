#!/bin/bash
# Bring the web demo up: orchestrator, control-plane tunnel, Pi agent.
# Kept as a script so restarts do not have to be typed through ssh quoting --
# and because `pkill -f demo_server.py` matches the ssh command line that
# invokes it, so the naive restart kills its own session.
APP=/home/spark/v2/interlock/app
VERINF_DIR="${VERINF:-$(cd "$APP/../.." && pwd)/VerInf}"
PY=/home/spark/v2/VerInf/venv/bin/python

# The Pi joins the Spark's own AP (SSID 2a-spark, NetworkManager ipv4.method=shared),
# so the control plane is a direct hop to the AP address. This replaced a reverse ssh
# tunnel that existed only because the Pi and Spark shared a client-isolated LAN and
# could not address each other at all. Set USE_TUNNEL=1 to fall back to that if the
# Pi is ever back on an isolated network.
cd "$APP" || exit 1

# When the boot service owns the orchestrator, this script must not also start
# one. Two copies race for the same port: the second fails to bind, and
# stop_server has already killed the managed one, which systemd then restarts
# underneath us. So if the unit is installed, leave the server alone entirely and
# just do the parts this script uniquely does -- the agent and the preflight.
UNIT=/etc/systemd/system/interlock-demo.service
managed() { [ -f "$UNIT" ]; }

# The port follows from who is running the server, because only one of them can
# have port 80: the service is granted it by CAP_NET_BIND_SERVICE, while a hand
# start from this script is an ordinary spark process that would simply fail to
# bind it. So unmanaged runs stay on the high port they have always used.
if managed; then PORT="${PORT:-80}"; else PORT="${PORT:-8770}"; fi
SPARK_URL="${SPARK_URL:-http://10.42.0.1:$PORT}"

stop_server() {
  ps -eo pid,args | grep "[p]ython -u demo_server.py" | awk '{print $1}' \
    | xargs -r kill -9 2>/dev/null
}

# systemd keeps its logs in the journal; a hand-started one is in /tmp.
server_log() {
  if managed; then journalctl -u interlock-demo -n "${1:-4}" --no-pager 2>/dev/null \
      || echo "  (no journal access -- try: sudo journalctl -u interlock-demo)"
  else tail -"${1:-4}" /tmp/demo_server.log 2>/dev/null; fi
}

case "${1:-start}" in
  start)
    if managed; then
      echo "orchestrator: managed by interlock-demo.service (leaving it alone)"
      systemctl is-active interlock-demo.service >/dev/null 2>&1 \
        || echo "  WARNING: the service is not active -- sudo systemctl start interlock-demo"
    else
      stop_server; sleep 1
      setsid "$PY" -u demo_server.py --port "$PORT" \
          > /tmp/demo_server.log 2>&1 < /dev/null &
      disown; sleep 4
    fi
    if [ "${USE_TUNNEL:-0}" = "1" ]; then
      PORT="$PORT" ./tunnel.sh start
      # the Pi's own end of the reverse forward, which is never the Spark's port
      SPARK_URL="http://127.0.0.1:${RPORT:-8770}"
    fi
    echo "agent will dial $SPARK_URL"
    ssh 2a-rpi "SPARK_URL=$SPARK_URL bash fpe/run_agent.sh start" 2>&1 | tail -3
    sleep 1
    # Preflight the pieces that fail LATE if they are missing. The GPU backend is
    # only touched at challenge time, so a dead one looks fine until the proof --
    # by which point someone is watching.
    echo "--- preflight ---"
    if timeout 3 python3 -c "import socket;socket.create_connection(('127.0.0.1',9917),2)" 2>/dev/null
    then echo "  backend  : UP (9917)"
    else echo "  backend  : DOWN -- run ./bringup.sh, the proof will fail without it"; fi
    # Liveness, not history: a log line from hours ago proves nothing, and catching a
    # board that died quietly is the whole point of this check.
    #
    # Sampling the counter twice was wrong. The servo line prints roughly every 12 s,
    # not continuously, so any window shorter than that reads a perfectly healthy
    # board as dead -- measured: identical values at t=3,6,9,12 s. Ask docker for
    # timestamps instead and check how old the newest servo line is. One shot, no
    # sleep, and it degrades honestly when there is no line at all.
    printf "  board       : "
    SERVO_MAX_AGE=${SERVO_MAX_AGE:-40}
    TS=$(docker logs -t --tail 60 ilk_server 2>&1 | grep -a "hits=" | tail -1 | awk '{print $1}')
    if [ -n "${TS:-}" ] && AGE=$(( $(date +%s) - $(date -d "$TS" +%s) )) 2>/dev/null; then
        if [ "$AGE" -le "$SERVO_MAX_AGE" ]; then
            echo "LIVE (last sync ${AGE}s ago)"
        else
            echo "QUIET -- last sync ${AGE}s ago. Power-cycle the interlock;"
            echo "                it goes quiet a few hours after power-up."; ok=0
        fi
    elif docker logs --tail 20 ilk_server 2>&1 | grep -qa "no sync stream"; then
        echo "QUIET -- wire half could not lock. Power-cycle the interlock."; ok=0
    else
        echo "no servo reading yet; re-run in a few seconds"; ok=0
    fi
    echo "--- orchestrator ---"; server_log 4
    # First run only: mint the per-layer weight commitments and the enrolment tree,
    # so the weight root the verifier is handed is one committed BEFORE the run
    # rather than one read back out of the proof. Skipped once enrolled (a file
    # check, no cost), and skipped when the board is quiet -- enrolment proves once
    # per layer over the wire and would just fail 22 times.
    if [ ! -f "${ENROLL_DIR:-$VERINF_DIR/.enroll}/model_policy.json" ]; then
        if [ "${ok:-1}" = "1" ]; then
            echo "--- first run: enrolling the model (~1 proof per layer) ---"
            if ! ./enroll.sh; then
                echo "  enrolment incomplete -- the demo still runs, but the weight"
                echo "  root is NOT pinned until ./enroll.sh succeeds"
            fi
        else
            echo "--- model not enrolled; run ./enroll.sh once the board is live ---"
        fi
    fi ;;
  stop)
    if managed; then
      echo "orchestrator: managed by systemd -- sudo systemctl stop interlock-demo"
    else stop_server; fi
    ./tunnel.sh stop >/dev/null 2>&1
    # Match on process shape, not on the string -- `pkill -f pi_agent.py` also
    # matches the ssh command line carrying it and kills its own remote shell.
    ssh 2a-rpi "ps -eo pid,args | awk '/[p]i_agent\\.py/ {print \$1}' | xargs -r sudo kill"
    echo stopped ;;
  status)
    if managed; then
      systemctl is-active interlock-demo.service | sed 's/^/service: /'
      systemctl is-enabled interlock-demo.service | sed 's/^/on boot: /'
    else
      ps -eo pid,args | grep -c "[p]ython -u demo_server.py" | sed 's/^/server procs: /'
    fi
    # Serving is the thing worth reporting; a live process that never bound the
    # port looks identical to a healthy one from the process table.
    curl -fsS -o /dev/null -m 3 "http://127.0.0.1:$PORT/" 2>/dev/null \
      && echo "http: OK on $PORT" || echo "http: NOT ANSWERING on $PORT"
    pgrep -f "8770:127.0.0.1:8770" >/dev/null && echo "tunnel: UP" || echo "tunnel: DOWN"
    server_log 5 ;;
esac
