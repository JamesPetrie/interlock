"""Send SPACED 802.3 LENGTH frames into one MPF300 port.

This gateware build emits ONE certificate per packet on each pipeline (request and
response). Each cert costs an HMAC (~hundreds of fabric cycles), and cert_build is a
pulse sink with no back-pressure: if a new packet's overall hash arrives while the
HMAC is still running, that cert is dropped. In production the cert period is ~1 s so
this never happens; in a host-driven test we must space the frames. 10 ms between
frames is ~1000x the HMAC latency, so every packet should yield a cert.

DATA = 16-byte header + payload. We keep DATA length != 148 so the forwarded copies
(which carry the original length) are trivially distinguishable from the 148-byte
cert frames that get muxed onto port 0.

usage: cert_send_spaced.py <iface> [count] [gap_ms] [plen]
"""
import socket
import struct
import sys
import time

ifn = sys.argv[1]
count = int(sys.argv[2]) if len(sys.argv) > 2 else 20
gapms = float(sys.argv[3]) if len(sys.argv) > 3 else 10.0
plen = int(sys.argv[4]) if len(sys.argv) > 4 else 16   # payload bytes after the 16B header

HDR = 16
data_len = HDR + plen
assert 16 <= data_len <= 1500
assert data_len != 148, "pick DATA len != 148 so forwarded copies != cert frames"

dst = b"\xde\xad\xbe\xef\x00\x01"   # arbitrary -> interlock forces to 02:..:0X
src = b"\xde\xad\xbe\xef\x00\x02"

s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
s.bind((ifn, 0))
for i in range(count):
    header = b"HDR\x00" + i.to_bytes(4, "big") + b"\x00" * 8     # 16B; content irrelevant to cert gen
    payload = bytes(((i + j) & 0xFF) for j in range(plen))
    data = header + payload
    frame = dst + src + struct.pack("!H", data_len) + data        # 802.3: DST SRC LEN DATA
    if len(frame) < 60:
        frame += b"\x00" * (60 - len(frame))
    s.send(frame)
    if i != count - 1:
        time.sleep(gapms / 1000.0)
print("sent %d spaced frames (DATA=%dB, L/T=%d, gap=%.1fms) on %s"
      % (count, data_len, data_len, gapms, ifn))
