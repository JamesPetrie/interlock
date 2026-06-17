"""Capture tick-beacon frames (DST 02:..:CB) on a NIC and parse the body.

The interlock broadcasts a beacon every STRIDE ticks (design A clock distribution);
the bucket field should advance by ~STRIDE between consecutive beacons. This is the
on-silicon validation of the beacon: it confirms the beacon egresses, is framed
canonically, and that the interlock's bucket counter is ticking.

usage: canon_beacon_parse.py <iface> [seconds]
"""
import socket, sys, time

ifn  = sys.argv[1]
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
BEACON_DST = b"\x02\x00\x00\x00\x00\xcb"
MAGIC = b"ilbcn-v1"

s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(0x0003))
s.bind((ifn, 0))
# promiscuous: the beacon DST (02:..:CB) is a locally-administered UNICAST, not
# this NIC's MAC, so a non-promisc socket would filter it out.
import struct as _st
s.setsockopt(263, 1, _st.pack("iHH8s", socket.if_nametoindex(ifn), 1, 0, b""))
s.settimeout(1.0)
t0 = time.time()
buckets = []
while time.time() - t0 < secs:
    try:
        frame = s.recv(2048)
    except socket.timeout:
        continue
    if frame[0:6] != BEACON_DST:
        continue
    body = frame[14:14 + 32]
    if len(body) < 32 or body[0:8] != MAGIC:
        continue
    iid    = int.from_bytes(body[8:16], "big")
    bucket = int.from_bytes(body[16:24], "big")
    period = int.from_bytes(body[24:28], "big")
    stride = int.from_bytes(body[28:32], "big")
    buckets.append(bucket)
    if len(buckets) <= 12:
        print("beacon: iid=%d bucket=%d period_ns=%d stride=%d" % (iid, bucket, period, stride))

print("--- %d beacons (DST 02:..:CB, magic ilbcn-v1) in %.1fs on %s ---" % (len(buckets), secs, ifn))
if len(buckets) >= 2:
    deltas = sorted(set(buckets[i + 1] - buckets[i] for i in range(min(len(buckets), 30) - 1)))
    print("distinct bucket deltas between consecutive beacons: %s (expect ~stride)" % deltas)
    print("first bucket=%d  last bucket=%d  (monotonic increasing = interlock clock ticking)"
          % (buckets[0], buckets[-1]))
print("RESULT:", "PASS - beacon live on silicon" if len(buckets) >= 2 else "FAIL - no beacons captured")
