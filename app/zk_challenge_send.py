"""Send one in-band ZK CHALLENGE control packet into the interlock (test/bring-up).

Control payload = MAGIC(8) || type(1)=CHALLENGE || body_len(2 BE) || body.
For the demo the body is request_id || req_cert || rsp_cert; here we send a minimal
body so the model-server routing + status/result reply can be tested in loopback.

usage: zk_challenge_send.py <iface> [request_id]
"""
import socket
import sys

IFACE = sys.argv[1]
RID = int(sys.argv[2]) if len(sys.argv) > 2 else 0
MAGIC = b"ILKZKCTL"
T_CHALLENGE = 1

header = b"CHL\x00" + RID.to_bytes(4, "big") + b"\x00" * 8     # 16B header
body = RID.to_bytes(8, "big")                                  # demo body (real: + certs)
payload = MAGIC + bytes([T_CHALLENGE]) + len(body).to_bytes(2, "big") + body
data = header + payload
dst = b"\xde\xad\xbe\xef\x00\x01"
src = b"\xde\xad\xbe\xef\x00\x02"
fr = dst + src + len(data).to_bytes(2, "big") + data
if len(fr) < 60:
    fr += b"\x00" * (60 - len(fr))

s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
s.bind((IFACE, 0))
s.send(fr)
print("sent CHALLENGE rid=%d (%dB data) on %s" % (RID, len(data), IFACE))
