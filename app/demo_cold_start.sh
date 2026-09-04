#!/usr/bin/env bash
# Cold-boot the interlock demo, from powered-off to a working screen.
#
# Written as a script rather than a checklist so it cannot drift from reality:
# every step below is the step that actually runs. It is idempotent -- safe to
# re-run at any point, and safe to run when things are already up.
#
#   ./demo_cold_start.sh          # fast mode (the demo), ~90 s to ready
#   ./demo_cold_start.sh sound    # full four-round proof, ~110 s per run
#   ./demo_cold_start.sh check    # preflight only, change nothing
#
# BEFORE running, physically:
#   1. Power the interlock board. It boots from flash; nothing to load.
#      Power-cycle it even if it looks on -- it goes quiet a few hours after
#      power-up and the symptom is a demo that fails at the first request.
#   2. Cables:  Pi eth0 -> RJ45 J30 (port 1, client, certificates)
#               Spark   -> RJ45 J15 (port 0, compute)
#      Swapping these silently breaks the demo: the verifier lands on the
#      compute link and no certificates reach it.
#   3. Power the Spark, then the Pi. The Pi joins the Spark's own AP
#      (SSID 2a-spark, autoconnect priority 10) with no venue network needed.
set -uo pipefail

MODE="${1:-fast}"
APP=/home/spark/v2/interlock/app
VERINF=/home/spark/v2/VerInf
PY="$VERINF/venv/bin/python"
cd "$APP" || exit 1

case "$MODE" in
  fast)  CHAL="$VERINF/analysis/subsample_challenge.py"; TQ=1 ;;
  sound) CHAL="$VERINF/analysis/interlock_challenge.py";  TQ=10 ;;
  check) CHAL=""; TQ="" ;;
  *) echo "usage: $0 [fast|sound|check]" >&2; exit 2 ;;
esac

hr() { printf '%.0s-' {1..64}; echo; }
ok=1

hr; echo "  INTERLOCK COLD START   mode=$MODE"; hr

# ---- 1. the Pi is reachable and on the Spark's AP -------------------------
printf "  pi          : "
if PIADDR=$(timeout 15 ssh -o BatchMode=yes -o ConnectTimeout=8 2a-rpi \
        "ip -4 -br addr show wlan0 | awk '{print \$3}'" 2>/dev/null) && [ -n "$PIADDR" ]; then
    case "$PIADDR" in
      10.42.*) echo "up, on the Spark AP ($PIADDR)" ;;
      *)       echo "up but NOT on the Spark AP ($PIADDR) -- run: ssh 2a-rpi 'nmcli con up 2a-spark'" ; ok=0 ;;
    esac
else
    echo "UNREACHABLE -- check it is powered and has joined 2a-spark"; ok=0
fi

# ---- 2. GPU backend + wire half ------------------------------------------
backend_up() { timeout 3 python3 -c "import socket;socket.create_connection(('127.0.0.1',9917),2)" 2>/dev/null; }

if [ "$MODE" != check ]; then
    printf "  backend     : "
    if backend_up; then
        echo "already up (9917)"
    else
        echo "starting (model load, ~20 s)"
        CHALLENGE_PY="$PY -u $CHAL" CHALLENGE_TQ="$TQ" ./bringup.sh > /tmp/coldstart-bringup.log 2>&1
        backend_up || { echo "  backend     : FAILED -- see /tmp/coldstart-bringup.log"; ok=0; }
    fi
else
    printf "  backend     : "; backend_up && echo "up (9917)" || { echo "DOWN"; ok=0; }
fi

# ---- 2b. the wire half ---------------------------------------------------
# A power-cycle of the interlock kills the wire half's lock, and it exits. The
# GPU backend on 9917 keeps answering throughout, so a "backend up, skip bringup"
# test walks straight past a dead ilk_server -- which is exactly what happened
# after the first power cycle. The wire half is a separate liveness question from
# the backend, so ask it separately.
printf "  wire half   : "
wire_locked() {
    docker ps --format '{{.Names}}' 2>/dev/null | grep -qx ilk_server || return 1
    TS=$(docker logs -t --tail 60 ilk_server 2>&1 | grep -a "hits=" | tail -1 | awk '{print $1}')
    [ -n "${TS:-}" ] || return 1
    [ $(( $(date +%s) - $(date -d "$TS" +%s) )) -le "${SERVO_MAX_AGE:-40}" ]
}
if wire_locked; then
    echo "locked"
elif [ "$MODE" = check ]; then
    echo "NOT LOCKED (check mode makes no changes)"; ok=0
else
    echo "restarting to re-lock"
    docker restart ilk_server >/dev/null 2>&1
    sleep 25
    wire_locked && echo "  wire half   : locked" \
                || { echo "  wire half   : FAILED to lock -- power-cycle the interlock"; ok=0; }
fi

# ---- 3. is the board actually emitting? ----------------------------------
# Two very different faults look identical from the servo log, and telling them
# apart decides whether you walk over to the board or just restart a container:
#
#   board quiet    -- nothing arriving on the wire at all. Needs a fabric reset,
#                     i.e. a power cycle of the interlock.
#   wire half wedged-- frames ARE arriving (1000 sync/s), but canon_tx's servo
#                     saturated, its re-bootstrap failed, and every request now
#                     dies with "send() before bootstrap()". It never self-heals.
#                     Restarting ilk_server fixes it in ~25 s.
#
# Observed: the second one, misreported as the first, sent us looking for a
# power switch when the board was perfectly healthy. So ask the NIC counter --
# it is the ground truth for "is anything on the cable" -- before blaming the board.
printf "  board       : "
RX0=$(cat /sys/class/net/"${IFACE:-enP7s7}"/statistics/rx_packets 2>/dev/null || echo 0)
sleep 3
RX1=$(cat /sys/class/net/"${IFACE:-enP7s7}"/statistics/rx_packets 2>/dev/null || echo 0)
RXD=$(( RX1 - RX0 ))
if [ "$RXD" -gt 100 ]; then
    echo "LIVE ($((RXD/3)) frames/s arriving)"
    # Board is emitting. Now: is our own wire half actually processing it?
    printf "  servo       : "
    # Presence of the error is not enough -- `docker restart` keeps the old log,
    # so a healthy container that has just re-locked still shows yesterday's
    # failures. Compare ORDER instead: wedged only if the newest bootstrap
    # failure is more recent than the newest successful lock.
    wedged() {
        local L="$(docker logs --tail 400 ilk_server 2>&1)"
        local lock fail
        lock=$(printf '%s\n' "$L" | grep -na "locked: bucket=" | tail -1 | cut -d: -f1)
        fail=$(printf '%s\n' "$L" | grep -na "send() before bootstrap()" | tail -1 | cut -d: -f1)
        [ -n "$fail" ] || return 1                 # never failed
        [ -n "$lock" ] || return 0                 # failed, never locked
        [ "$fail" -gt "$lock" ]                    # failed AFTER the last lock
    }
    if wedged; then
        if [ "$MODE" = check ]; then
            echo "WEDGED (bootstrap failed) -- run without 'check' to restart it"; ok=0
        else
            echo "WEDGED (bootstrap failed) -- restarting ilk_server"
            docker restart ilk_server >/dev/null 2>&1; sleep 25
            if docker logs --tail 20 ilk_server 2>&1 | grep -qa "locked: bucket="; then
                echo "  servo       : re-locked"
            else
                echo "  servo       : STILL WEDGED -- power-cycle the interlock"; ok=0
            fi
        fi
    else
        echo "ok"
    fi
else
    echo "QUIET -- only $RXD frames in 3s on ${IFACE:-enP7s7}."
    echo "                The board is not emitting. Power-cycle the interlock."
    ok=0
fi

# ---- 4. orchestrator + Pi agent ------------------------------------------
if [ "$MODE" != check ]; then
    echo "  orchestrator: starting"
    ./demo_up.sh start 2>&1 | sed 's/^/                /'
fi

hr
if [ "$ok" = 1 ]; then
    IP=$(ip -4 -br addr show wlP9s9 2>/dev/null | awk '{print $3}' | cut -d/ -f1)
    echo "  READY"
    # Port follows whoever is serving: the boot service takes 80, a hand start
    # from demo_up.sh stays on 8770. Print the one that is actually answering
    # rather than a guess -- these two lines are what somebody types.
    if [ -f /etc/systemd/system/interlock-demo.service ]; then DP=80; else DP=8770; fi
    echo "    demo      http://10.42.0.1:$DP/        (from the Pi's network)"
    [ -n "$IP" ] && echo "    or        http://${IP%%/*}:$DP/"
    echo "    headless  $PY -u demo_probe.py"
else
    echo "  NOT READY -- fix the lines above and re-run."
fi
hr
