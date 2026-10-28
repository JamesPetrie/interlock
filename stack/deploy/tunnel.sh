#!/usr/bin/env bash
# Reverse SSH tunnel to the ingress box: exposes the frontend's HTTP port on
# the ingress box's loopback, where the reverse proxy forwards to it.
#
#   tunnel.sh <user@ingress-host> <ingress-port> <frontend-host:port>
#
# Reconnects forever; run it in the background from the frontend job so the
# tunnel lives and dies with the frontend. The key ~/.ssh/iltunnel is
# authorized on the ingress box with restrict,port-forwarding,permitlisten.
set -u
target=$1; rport=$2; local=$3
while true; do
    ssh -N -i "$HOME/.ssh/iltunnel" -o BatchMode=yes -o ExitOnForwardFailure=yes \
        -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
        -R "127.0.0.1:$rport:$local" "$target"
    echo "tunnel: ssh exited ($?), reconnecting in 5 s" >&2
    sleep 5
done
