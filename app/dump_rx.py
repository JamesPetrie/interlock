"""Dump the canonical DATA the interlock actually forwards to this port.

traffic_commit.md says the commitment is a tap on the *emitted* stream, so the
certificate covers the bytes that leave the device -- not necessarily the bytes
the sender put on the wire. This prints what actually arrives, so the record
hash can be computed over the right bytes.
"""
import hashlib
import struct
import sys

sys.path.insert(0, "/app")
import canon_tx

IFACE = sys.argv[1] if len(sys.argv) > 1 else "enP7s7"
SERVER = b"\x02\x00\x00\x00\x00\x02"
CLIENT = b"\x02\x00\x00\x00\x00\x01"

port = canon_tx.CanonPort(IFACE, req=False, tuned=False, verbose=True,
                          accept_dst=SERVER, accept_src=CLIENT)
port.lock()
port.start()
print("dumping forwarded DATA on %s (Ctrl-C to stop)" % IFACE, flush=True)

n = 0
while n < 6:
    data = port.recv(timeout=30)
    if data is None:
        print("(timeout)", flush=True)
        break
    if len(data) == 224:                      # a certificate, not forwarded traffic
        continue
    n += 1
    hdr, pl = data[:64], data[64:]
    dlen, bkt, ident = struct.unpack("!IIQ", hdr[:16])
    print("\n--- forwarded DATA #%d: %d bytes ---" % (n, len(data)), flush=True)
    print("  hdr[0:16] : %s" % hdr[:16].hex(), flush=True)
    print("    dlen=%d  declared_bucket=%d  ident=%d" % (dlen, bkt, ident), flush=True)
    print("  hdr[16:64]: %s%s" % (hdr[16:64].hex()[:48],
                                  "" if any(hdr[16:64]) else "  (all zero)"), flush=True)
    print("  payload   : %d bytes %s" % (len(pl), pl[:24].hex()), flush=True)
    inner = hashlib.sha256(hdr + hashlib.sha256(pl).digest()).digest()
    print("  record(len=payload) : %s"
          % hashlib.sha256(struct.pack(">H", len(pl)) + inner).digest().hex(), flush=True)
    print("  record(len=total)   : %s"
          % hashlib.sha256(struct.pack(">H", len(data)) + inner).digest().hex(), flush=True)
port.close()
