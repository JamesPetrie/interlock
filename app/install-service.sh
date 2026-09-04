#!/bin/bash
# Install the demo as boot services on the Spark: the orchestrator on port 80 and
# the control-plane tunnel that lets the Pi reach it.
#
#   sudo ./install-service.sh          install, enable, start both
#   sudo ./install-service.sh remove   stop, disable, delete both
#
# WHY A SCRIPT. Several steps are easy to get wrong from memory: the capability
# that lets a non-root process bind port 80, the two hardening options that must
# NOT be set (see interlock-demo.service), verifying each unit before enabling it
# so the machine does not reboot into a demo that will not come up, and waiting
# for the port to actually SERVE rather than for systemd to merely fork.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
# Order matters only for readability; systemd starts them in parallel and each
# retries on its own. The backend is the one that takes minutes, not seconds.
UNITS=(interlock-backend.service interlock-demo.service interlock-tunnel.service)
PORT=80

if [ "$(id -u)" -ne 0 ]; then echo "needs root: sudo $0 ${1:-}" >&2; exit 1; fi

if [ "${1:-install}" = "remove" ]; then
  for u in "${UNITS[@]}"; do systemctl disable --now "$u" 2>/dev/null || true
    rm -f "/etc/systemd/system/$u"; done
  systemctl daemon-reload
  echo "removed: ${UNITS[*]}"
  exit 0
fi

for u in "${UNITS[@]}"; do [ -f "$DIR/$u" ] || { echo "missing $DIR/$u" >&2; exit 1; }; done

# Stop our own units first. Re-running this over an already-installed demo is the
# normal case (upgrading a unit, adding the tunnel), and the orchestrator holding
# port 80 is then OUR service, not a conflict -- an earlier cut of this check
# refused to run at all in exactly that situation.
for u in "${UNITS[@]}"; do systemctl stop "$u" 2>/dev/null || true; done
sleep 1

# Anything STILL on port 80 is something else, and it will make the orchestrator
# fail to bind five seconds after this script has reported success. Find out now.
if ss -ltnH "sport = :$PORT" 2>/dev/null | grep -q .; then
  echo "something other than this demo is listening on port $PORT:" >&2
  ss -ltnp "sport = :$PORT" 2>/dev/null >&2
  echo "stop it first, or the orchestrator will not bind." >&2
  exit 1
fi
# A hand-started copy from demo_up.sh would hold the port and answer as if it were
# the service; and a hand-started tunnel would collide with the tunnel unit's
# reverse bind. Clear both out. (Match on shape so this line does not kill itself.)
pkill -f "python -u demo_server\.py" 2>/dev/null || true
ps -eo pid,args | awk '/ssh -N/ && /-R 8770:/ {print $1}' | xargs -r kill 2>/dev/null || true
sleep 1

for u in "${UNITS[@]}"; do
  install -m 644 -o root -g root "$DIR/$u" "/etc/systemd/system/$u"
  systemd-analyze verify "/etc/systemd/system/$u" \
    || { echo "unit $u did not verify -- not enabling it" >&2; rm -f "/etc/systemd/system/$u"; exit 1; }
done

systemctl daemon-reload
for u in "${UNITS[@]}"; do systemctl enable "$u"; systemctl restart "$u"; done

# Starting is not serving: the orchestrator hashes 2.2 GB of weights before it
# listens. Wait for the socket, then confirm the page really answers.
echo -n "waiting for port $PORT "
for _ in $(seq 1 60); do
  if curl -fsS -o /dev/null -m 2 "http://127.0.0.1:$PORT/" 2>/dev/null; then
    echo; echo "up and serving on port $PORT"
    for u in "${UNITS[@]}"; do
      printf '  %-26s %s / %s\n' "$u" "$(systemctl is-enabled "$u")" "$(systemctl is-active "$u")"
    done
    exit 0
  fi
  echo -n .; sleep 2
done

echo; echo "did not answer on port $PORT within 120s. Recent logs:" >&2
journalctl -u interlock-demo -n 15 --no-pager >&2
exit 1
