#!/bin/bash
# Capture + parse tick-beacon frames (DST 02:..:CB) on the live MPF300 bridge.
# Beacons (and certs) egress port 0. Tries both host NICs so a swapped cable
# still finds them. PASS = beacons captured with a monotonically increasing bucket.
# usage: canon_beacontest.sh [seconds]
set -u
SECS=${1:-5}
# make sure both NICs are admin-up so the PHY link can come up
docker run --rm --network host --cap-add NET_ADMIN nicolaka/netshoot \
  bash -c "ip link set enP7s7 up; ip link set enxb8fbb3b1f53c up; sleep 6" >/dev/null 2>&1
for IF in enP7s7 enxb8fbb3b1f53c; do
  echo "=== $IF (carrier=$(cat /sys/class/net/$IF/carrier 2>/dev/null)) ==="
  docker run --rm --network host --cap-add NET_RAW -v /home/claude/fpe:/fpe \
    python:3-slim python3 /fpe/canon_beacon_parse.py "$IF" "$SECS"
done
