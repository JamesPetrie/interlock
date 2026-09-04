"""Does acceptance depend on payload size on this port?

The server's 0-byte bootstrap probes are accepted 24/24 while its 112-byte
response is rejected 10/10, which points at size (or the time cost of putting a
bigger frame on the wire) rather than at the bucket lock.
"""
import sys
import time

sys.path.insert(0, "/app")
import canon_tx

IFACE = sys.argv[1] if len(sys.argv) > 1 else "enP7s7"
port = canon_tx.open_port(IFACE, req=False)

import os
GAP = float(os.environ.get("GAP", "0.01"))
for size in (112,):
    ok = 0
    n = 6
    for _ in range(n):
        try:
            port.send_confirmed(b"\x5a" * size)
            ok += 1
        except RuntimeError as e:
            print("   attempt failed:", e, flush=True)
        time.sleep(GAP)
    print("payload %4d B, gap %.1fs -> accepted %2d/%d" % (size, GAP, ok, n), flush=True)
port.close()
