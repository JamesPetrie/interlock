"""Send Ethernet II / TYPE frames (EtherType >= 1536) into one MPF300 port to
test the eth_sanitize deframe's TYPE-frame drop. The L/T field is set to a real
EtherType (default 0x86DD IPv6), which exceeds ETH_LEN_MAX=1500, so the deframe
must suppress the whole frame (beats_total forced to 0 -> no AXI beats emitted).
This is the exact frame class that previously WEDGED the bridge.

usage: canon_send_type.py <iface> [count] [datalen] [ethertype_hex]
"""
import socket, struct, sys

ifn   = sys.argv[1]
count = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
dlen  = int(sys.argv[3]) if len(sys.argv) > 3 else 64
etype = int(sys.argv[4], 16) if len(sys.argv) > 4 else 0x86DD  # IPv6 EtherType

assert etype >= 1536, "EtherType must be >= 1536 to be a TYPE frame"
base = b"TYPEFRAME" + bytes(range(256))
payload = (base * (dlen // len(base) + 1))[:dlen]
dst = b"\xde\xad\xbe\xef\x00\x01"     # arbitrary; bridge would force to 02:..:0X
src = b"\xde\xad\xbe\xef\x00\x02"
frame = dst + src + struct.pack("!H", etype) + payload   # EtherII: DST SRC TYPE DATA
if len(frame) < 60:
    frame += b"\x00" * (60 - len(frame))

s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
s.bind((ifn, 0))
for _ in range(count):
    s.send(frame)
print("sent %d Ethernet-II TYPE frames (EtherType=0x%04x > 1500, payload 'TYPEFRAME...') on %s"
      % (count, etype, ifn))
