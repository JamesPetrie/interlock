#!/bin/bash
# Validate Peter's eth_sanitize TYPE-frame drop on the live MPF300 bridge.
# Three phases, in one direction (pass send/recv ifaces to flip if cables swapped):
#   1. baseline  : LENGTH frames forward            (expect forwarded > 0)
#   2. TYPE drop : EtherType 0x86DD frames dropped  (expect forwarded == 0)
#   3. re-check  : LENGTH frames STILL forward      (expect forwarded > 0)
# Phase 3 is the real regression test: the original bug WEDGED the bridge on a
# TYPE frame, so it would forward 0 here. PASS iff base1>0 AND typed==0 AND base2>0.
#
# usage: type_drop.sh [send-iface] [recv-iface] [count] [plen]
set -u
S=${1:-enP7s7}
R=${2:-enxb8fbb3b1f53c}
N=${3:-3000}
PLEN=${4:-64}
FILT='ether dst 02:00:00:00:00:01 or ether dst 02:00:00:00:00:02'
SEND=python:3-slim
CAP=nicolaka/netshoot

run() {  # $1=script $2=label
  docker rm -f iltcap >/dev/null 2>&1
  docker run -d --name iltcap --network host --cap-add NET_RAW --cap-add NET_ADMIN \
    $CAP tcpdump -e -nn -c 300 -i "$R" $FILT >/dev/null 2>&1
  sleep 2.5
  docker run --rm --network host --cap-add NET_RAW -v /home/claude/fpe:/fpe \
    $SEND python3 /fpe/$1 "$S" "$N" "$PLEN" >/dev/null 2>&1
  sleep 2
  docker logs iltcap > /tmp/td_$2.txt 2>&1
  docker rm -f iltcap >/dev/null 2>&1
  grep -cE '02:00:00:00:00:0[12]' /tmp/td_$2.txt
}

echo "=== direction $S -> $R, N=$N plen=$PLEN ==="
b1=$(run canon_send.py base1)
echo "  1. LENGTH baseline      : forwarded(forced-DST) = $b1   (expect > 0)"
td=$(run canon_send_type.py typed)
echo "  2. TYPE (0x86DD) frames : forwarded(forced-DST) = $td   (expect == 0, DROPPED)"
b2=$(run canon_send.py base2)
echo "  3. LENGTH re-check      : forwarded(forced-DST) = $b2   (expect > 0, NO WEDGE)"
echo
if [ "$b1" -gt 0 ] && [ "$td" -eq 0 ] && [ "$b2" -gt 0 ]; then
  echo "RESULT: PASS  (LENGTH forwards, TYPE dropped cleanly, bridge survives)"
else
  echo "RESULT: FAIL  (b1=$b1 typed=$td b2=$b2)"
fi
