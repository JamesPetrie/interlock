#!/usr/bin/env python3
"""Record exactly how long the interlock runs before it stops.

The board stops emitting its 1 kHz sync stream after some hours, with the PHY
link still up and nothing logged anywhere. The first clean measurement of that
came out at 3 h 59 m 53 s -- four hours to within seconds, from a board that was
idle at the time. A number that precise is a timer expiring, not a fault
accumulating, but it is one data point. This script turns the next power cycle
into a second one.

It is deliberately passive: it opens a raw socket, reads the sync stream, and
writes a line per minute. It never transmits, so it cannot itself be what
wedges the bridge, and it can run alongside the app.

    sudo python3 uptime_watch.py eth0 [logfile]

Each line is `<wall clock ISO> <bucket> <seconds since power-on>`, where the
bucket counter free-runs from the fabric's reset -- so the board's own uptime is
readable directly from the stream and does not depend on when this started. When
sync stops, it writes a FINAL line with the last bucket seen and exits non-zero,
which is the measurement: `bucket / 1000` seconds is the lifetime.
"""
import datetime
import socket
import struct
import sys
import time

HDR_BYTES = 64
SYNC_ID = 1
GAP_FATAL_S = 5.0       # sync is 1 kHz; 5 s of silence is a stop, not a hiccup
LOG_EVERY_S = 60.0


def parse_sync(fr):
    """(bucket, first_arr) for a sync frame, else None."""
    if len(fr) < 14 + HDR_BYTES or fr[12:14] != struct.pack("!H", HDR_BYTES):
        return None
    p = fr[14:14 + HDR_BYTES]
    first_arr, bucket = struct.unpack("!II", p[0:8])
    if struct.unpack("!Q", p[8:16])[0] != SYNC_ID:
        return None
    return bucket, first_arr


def main():
    iface = sys.argv[1] if len(sys.argv) > 1 else "eth0"
    path = sys.argv[2] if len(sys.argv) > 2 else "interlock_uptime.log"
    out = open(path, "a", buffering=1)

    def emit(msg):
        line = "%s %s" % (datetime.datetime.now().astimezone().isoformat(timespec="seconds"), msg)
        print(line, flush=True)
        out.write(line + "\n")

    s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(0x0003))
    s.bind((iface, 0))
    # Forwarded frames carry the interlock's forced MACs, not this NIC's, so the
    # kernel drops them unless the interface is promiscuous.
    s.setsockopt(263, 1, struct.pack("iHH8s", socket.if_nametoindex(iface), 1, 0, b""))
    s.settimeout(1.0)

    emit("WATCH start iface=%s" % iface)
    last_bkt = None
    last_seen = time.monotonic()
    last_log = 0.0
    while True:
        try:
            fr = s.recv(2048)
        except socket.timeout:
            fr = None
        now = time.monotonic()
        if fr is not None:
            sy = parse_sync(fr)
            if sy:
                last_bkt, last_seen = sy[0], now
                if now - last_log >= LOG_EVERY_S:
                    last_log = now
                    emit("bucket=%d uptime=%.3fs (%.3f h)"
                         % (last_bkt, last_bkt / 1000.0, last_bkt / 3.6e6))
        if last_bkt is None:
            if now - last_seen > GAP_FATAL_S:
                emit("FINAL no sync stream at all -- board already down, or wrong port")
                return 2
            continue
        if now - last_seen > GAP_FATAL_S:
            # The measurement. The bucket counter is the board's own clock since
            # reset, so this is its lifetime regardless of when the watch started.
            emit("FINAL sync stopped: last bucket=%d -> board ran %.3fs (%.4f h)"
                 % (last_bkt, last_bkt / 1000.0, last_bkt / 3.6e6))
            return 1


if __name__ == "__main__":
    sys.exit(main())
