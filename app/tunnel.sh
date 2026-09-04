#!/bin/bash
# Control-plane route: Pi -> Spark. The Pi's eth0 is the raw interlock wire (no IP)
# and the shared wireless LAN has client isolation, so the only working path is the
# Spark's outbound ssh to the Pi. Reverse-forward the demo server over it: the Pi
# then reaches the orchestrator at 127.0.0.1:$RPORT.
#
# The two ends are deliberately different ports. The orchestrator listens on 80 on
# the Spark (a boot service, granted CAP_NET_BIND_SERVICE), but the LISTENING end
# of a reverse forward is created by sshd on the Pi as the ordinary rpi user, and
# it cannot bind a privileged port. So the Pi keeps its high port and this maps it
# onto whatever the Spark is serving.
#
# This is the CONTROL plane only. The prompt and response still cross the certified
# wire; nothing here carries payload.
RPORT="${RPORT:-8770}"   # the Pi's local end -- must stay unprivileged
PORT="${PORT:-80}"       # the Spark's end -- where the orchestrator actually is
case "${1:-start}" in
  start)
    ps -eo pid,args | awk '/ssh -N/ && /-R/ {print $1}' | xargs -r kill 2>/dev/null; sleep 1
    nohup ssh -N -o BatchMode=yes -o ExitOnForwardFailure=yes \
        -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
        -R "$RPORT:127.0.0.1:$PORT" 2a-rpi >/tmp/tunnel.log 2>&1 &
    disown
    sleep 5
    if ps -eo args | grep -q "^ssh -N"; then echo "TUNNEL UP"
    else echo "TUNNEL DEAD"; cat /tmp/tunnel.log; fi ;;
  stop) ps -eo pid,args | awk '/ssh -N/ && /-R/ {print $1}' | xargs -r kill 2>/dev/null
        echo "TUNNEL STOPPED" ;;
  test) ssh 2a-rpi curl -s -m 6 -o /dev/null -w 'pi->orchestrator:%{http_code}\n' \
             "http://127.0.0.1:$RPORT/" ;;
esac
