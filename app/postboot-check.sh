#!/bin/bash
# Did the demo come back by itself?
#
# Run after a reboot with nothing else touched. It checks the things that are
# separately capable of being down while the others look fine -- a service can be
# active without ever having bound its port, a bound port can serve the shell of a
# page whose bundle is missing, and the whole dashboard can look healthy while the
# backend the proof needs is gone.
#
# Exit 0 only if every REQUIRED check passes. The board and the Pi agent are
# reported but not required: the board is powered separately, and the Pi is a
# different machine that this reboot did not touch.
PORT="${PORT:-80}"
fail=0
ok()   { printf '  \033[32mok\033[0m    %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; fail=1; }
note() { printf '  --    %s\n' "$1"; }

echo "uptime: $(uptime -p)  (booted $(uptime -s))"
echo
echo "orchestrator"
systemctl is-enabled interlock-demo.service >/dev/null 2>&1 \
  && ok "enabled at boot" || bad "NOT enabled at boot"
systemctl is-active interlock-demo.service >/dev/null 2>&1 \
  && ok "active" || bad "not active"

# How long after boot did it start serving? This is the number that says whether
# someone opening the case has to wait, and it is invisible from is-active.
S=$(systemctl show interlock-demo.service -p ActiveEnterTimestampMonotonic --value 2>/dev/null)
[ -n "$S" ] && [ "$S" != "0" ] && note "started $((S/1000000))s after boot"

echo
echo "http on :$PORT"
code=$(curl -s -o /dev/null -m 10 -w '%{http_code}' "http://127.0.0.1:$PORT/" 2>/dev/null)
[ "$code" = "200" ] && ok "/ -> 200" || bad "/ -> ${code:-no answer}"
for p in /lab /favicon.svg; do
  c=$(curl -s -o /dev/null -m 10 -w '%{http_code}' "http://127.0.0.1:$PORT$p" 2>/dev/null)
  [ "$c" = "200" ] && ok "$p -> 200" || bad "$p -> ${c:-no answer}"
done
# The shell is served even when the build is missing -- demo_server falls back to
# a 503 with instructions -- so check that the JS bundle is really there.
if curl -s -m 10 "http://127.0.0.1:$PORT/" 2>/dev/null | grep -q '_app/immutable'; then
  ok "dashboard bundle referenced"
else bad "page served but no bundle -- run: cd dashboard && pnpm build"; fi

# Socket.IO is how every value reaches the page; a dashboard that loads and never
# populates looks like a hang rather than a fault.
sio=$(curl -s -m 10 -o /dev/null -w '%{http_code}' \
      "http://127.0.0.1:$PORT/socket.io/?EIO=4&transport=polling" 2>/dev/null)
[ "$sio" = "200" ] && ok "socket.io handshake" || bad "socket.io -> ${sio:-no answer}"

echo
echo "control-plane tunnel (optional -- the agent talks to the AP directly)"
# The Pi rides the Spark's own hotspot and dials http://10.42.0.1:80, so the
# demo needs no internet on either box. The reverse-ssh tunnel is kept only as
# the admin/fallback path for when the Pi is on an isolated venue LAN; without
# internet it restart-loops, which is expected and must not fail this check.
systemctl is-active interlock-tunnel.service >/dev/null 2>&1 \
  && note "tunnel up (internet available)" \
  || note "tunnel down (fine offline; agent path is the AP)"

echo
echo "proof backend"
# Two separate processes, and it is easy to check the wrong one. 9917 is
# model_backend.py on the HOST (it needs CUDA, so it is not in the container);
# ilk_server is the wire half in docker, which talks to it over loopback and
# logs only a warning when it is missing. Either can be down while the other
# looks perfectly healthy, and the demo needs both.
systemctl is-enabled interlock-backend.service >/dev/null 2>&1 \
  && ok "backend enabled at boot" || bad "backend NOT enabled at boot"
if timeout 4 python3 -c "import socket;socket.create_connection(('127.0.0.1',9917),3)" 2>/dev/null
then ok "model_backend listening on 9917 (GPU)"
else bad "model_backend DOWN on 9917 -- the page works, the proof will not"; fi
docker inspect -f '{{.State.Running}}' ilk_server 2>/dev/null | grep -q true \
  && ok "ilk_server container running (wire half)" \
  || bad "ilk_server container not running"
docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' ilk_server 2>/dev/null \
  | grep -q "unless-stopped" && ok "container set to restart on boot" \
  || bad "container restart policy is not unless-stopped"

echo
echo "not required by this reboot"
TS=$(docker logs -t --tail 60 ilk_server 2>&1 | grep -a "hits=" | tail -1 | awk '{print $1}')
if [ -n "${TS:-}" ] && AGE=$(( $(date +%s) - $(date -d "$TS" +%s) )) 2>/dev/null \
   && [ "$AGE" -le 40 ]; then note "board LIVE (last sync ${AGE}s ago)"
else note "board quiet -- power-cycle the interlock when you want a real run"; fi
AST=$(timeout 15 ssh -o BatchMode=yes -o ConnectTimeout=6 2a-rpi \
     "systemctl is-active interlock-agent.service 2>/dev/null" 2>/dev/null || echo unreachable)
note "pi agent service: $AST (separate machine; its own boot unit)"
# The one signal that the whole live path closed: the orchestrator warmed an agent
# more recently than it lost one.
W=$(journalctl -u interlock-demo --no-pager 2>/dev/null | grep -aE "agent warm|agent gone" | tail -1)
case "$W" in
  *"agent warm"*) note "orchestrator has a warm agent (wire connected)" ;;
  *"agent gone"*) note "orchestrator's agent has disconnected -- check the tunnel and the board" ;;
  *)              note "orchestrator has not seen an agent yet" ;;
esac

echo
[ "$fail" = 0 ] && echo "PASS -- the demo came back on its own." \
                || echo "FAIL -- see the lines above."
exit "$fail"
