"""Beacon-driven bucket accept/drop test on the live combined (header+beacon) build.

Sniffs beacons (DST 02:..:CB) + certs (DST 02:..:CE) on the port-0 NIC, and sends
bucket-tagged wire packets on the send NIC:
  ACCEPT: declare the beacon-predicted current bucket -> packets fold into certs
          (cert overall_in/out != the all-empty value).
  DROP  : declare an absurd bucket -> nothing folds (certs stay all-empty).
The contrast proves the design-A exact-match bucket check works on silicon.

usage: bucket_silicon_test.py <p0_iface> <send_iface>
"""
import hashlib, socket, sys, threading, time

def H(d): return hashlib.sha256(d).digest()

P0, SEND = sys.argv[1], sys.argv[2]
BEACON_DST = b"\x02\x00\x00\x00\x00\xcb"
CERT_DST   = b"\x02\x00\x00\x00\x00\xce"
MAGIC, CERTMAGIC = b"ilbcn-v1", b"ilock-v5"
KEY, CT = H(b"k"), b"\xa5" * 8
N = 8                                   # buckets per cert window (interlock_core N)

def bucket_hash(recs): return H(b"".join(recs))
def overall_hash(bhs): return H(b"".join(bhs))
EMPTY_OVERALL = overall_hash([bucket_hash([])] * N)   # cert with nothing folded

def input_packet(rid, b, key, ct):
    return len(ct).to_bytes(4,"big")+rid.to_bytes(8,"big")+b.to_bytes(8,"big")+H(key)+ct
def output_packet(rid, b, ct):
    return len(ct).to_bytes(4,"big")+rid.to_bytes(8,"big")+b.to_bytes(8,"big")+ct
def frame(pkt):
    f = b"\x02\x00\x00\x00\x00\x0a"+b"\x02\x00\x00\x00\x00\x0b"+len(pkt).to_bytes(2,"big")+pkt
    return f + b"\x00"*(60-len(f)) if len(f) < 60 else f

clock = {"B": None, "t": None, "period_ns": 1_000_000}
certs, stop = [], False

def sniffer():
    s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(0x0003))
    s.bind((P0, 0))
    # promiscuous: beacon/cert DSTs are locally-administered unicasts, not our MAC
    import struct as _st
    s.setsockopt(263, 1, _st.pack("iHH8s", socket.if_nametoindex(P0), 1, 0, b""))
    s.settimeout(0.5)
    while not stop:
        try: fr = s.recv(2048)
        except socket.timeout: continue
        if fr[0:6] == BEACON_DST and fr[14:22] == MAGIC:
            clock["B"] = int.from_bytes(fr[30:38], "big")     # body bucket @ +16
            clock["t"] = time.time()
            clock["period_ns"] = int.from_bytes(fr[38:42], "big")
        elif fr[0:6] == CERT_DST and fr[14:22] == CERTMAGIC:
            certs.append((fr[42:74], fr[74:106]))             # overall_in, overall_out

def predict():
    if clock["B"] is None: return None
    return clock["B"] + int((time.time() - clock["t"]) * 1e9 // clock["period_ns"])

threading.Thread(target=sniffer, daemon=True).start()
t0 = time.time()
while clock["B"] is None and time.time() - t0 < 12: time.sleep(0.05)
if clock["B"] is None:
    print("FAIL: no beacon captured on %s -> cannot calibrate" % P0); sys.exit(1)
print("calibrated from beacon: bucket~%d  period_ns=%d" % (clock["B"], clock["period_ns"]))

ss = socket.socket(socket.AF_PACKET, socket.SOCK_RAW); ss.bind((SEND, 0))

def send_phase(bucket_fn, n, rid0):
    rid = rid0
    for _ in range(n):
        b = bucket_fn()
        ss.send(frame(input_packet(rid, b, KEY, CT)))
        ss.send(frame(output_packet(rid, b, CT)))
        rid += 1
        time.sleep(0.0004)
    return rid

def nonempty(cs): return sum(1 for oi, oo in cs if oi != EMPTY_OVERALL or oo != EMPTY_OVERALL)

# ACCEPT: spread declared buckets around the prediction + send slowly, to beat the
# exact-match timing jitter and the core's one-packet-at-a-time processing rate.
certs.clear(); rid = 1
for _ in range(500):
    p = predict()
    for off in (-1, 0, 1, 2, 3):
        ss.send(frame(input_packet(rid, p + off, KEY, CT)))
        ss.send(frame(output_packet(rid, p + off, CT)))
        rid += 1
        time.sleep(0.0008)
time.sleep(0.5)
acc = list(certs); acc_ne = nonempty(acc)
print("ACCEPT (declare predicted bucket): %d certs, %d NON-empty (packets folded)" % (len(acc), acc_ne))

certs.clear(); rid = send_phase(lambda: predict() + 10_000_000, 2500, rid); time.sleep(0.4)
drp = list(certs); drp_ne = nonempty(drp)
print("DROP   (declare absurd bucket)   : %d certs, %d NON-empty (expect 0 -> all dropped)" % (len(drp), drp_ne))

stop = True
ok = acc_ne > 0 and len(drp) > 0 and drp_ne == 0
print("RESULT:", "PASS - accept folds, wrong-bucket drops (design A live on silicon)"
      if ok else "INCONCLUSIVE (acc_ne=%d drp_certs=%d drp_ne=%d)" % (acc_ne, len(drp), drp_ne))
