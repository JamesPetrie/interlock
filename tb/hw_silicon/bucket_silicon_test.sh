#!/bin/bash
# Beacon-driven bucket accept/drop test on the live combined build.
# usage: bucket_silicon_test.sh [p0_iface(beacon/cert egress)] [send_iface]
set -u
P0=${1:-enP7s7}
SEND=${2:-enxb8fbb3b1f53c}
docker run --rm --network host --cap-add NET_ADMIN nicolaka/netshoot \
  bash -c "ip link set $P0 up; ip link set $SEND up; sleep 6" >/dev/null 2>&1
docker run --rm --network host --cap-add NET_RAW -v /home/claude/fpe:/fpe \
  python:3-slim python3 /fpe/bucket_silicon_test.py "$P0" "$SEND"
