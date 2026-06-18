#!/usr/bin/env python3
"""infcli — MacBook port-0 driver for the interlock (#3).

Runs on the MacBook (the prover frontend + demo verifier). Drives one inference
request packet at a time through the interlock on port 0, captures the response and
the per-packet certificates, verifies the certs locally, logs everything, and on
`challenge` ships the certified bytes to the Spark over out-of-band SSH to run the
ZK proof + plaintext-match check.

Wire contract + cert format: see inference-cli-app.md. The framing and cert
parse/verify here are the exact code verified on silicon (6/6 tau, 6/6 overall); the
only platform-specific part is the raw-Ethernet transport, which uses scapy/libpcap
(macOS has no AF_PACKET). scapy puts the NIC in promiscuous mode for capture, which is
required to receive frames addressed to the interlock's forced MACs.

  pip install scapy           # needs libpcap; run with sudo (raw L2 + /dev/bpf)

  sudo python3 infcli.py --iface en7 send --text "hello"
  sudo python3 infcli.py --iface en7 log
  sudo python3 infcli.py --iface en7 verify 3
  sudo python3 infcli.py --iface en7 challenge 3 --spark-host spark-c191.local

HARD: one packet in flight at a time, spaced (per-packet cert HMAC has no
back-pressure — a flood wedges the interlock). The send loop enforces --gap-ms.
"""
import argparse
import binascii
import hashlib
import hmac
import json
import os
import struct
import time

SERVER = bytes.fromhex("020000000002")     # interlock forced SRC of port-0 egress (responses + certs)
CLIENT = bytes.fromhex("020000000001")     # interlock forced DST of port-0 egress
HDR = 16
CERT_LEN = 148
DEFAULT_KEY = (2).to_bytes(32, "big")      # test-build cert key (cert_build .key(2))
LOGDIR = os.path.expanduser("~/.infcli")

# In-band ZK control protocol (payload-prefix marker; see model_server.py / spec §6,§9).
# The challenge + status/result travel as normal packets through the interlock — no
# out-of-band channel. The proof stays on the Spark (verify-on-Spark); only these small
# control messages cross.
MAGIC = b"ILKZKCTL"
T_CHALLENGE, T_STATUS, T_RESULT = 1, 2, 3

# ---------------- verified wire helpers (identical to the bring-up scripts) -------

def frame(data: bytes) -> bytes:
    dst = b"\xde\xad\xbe\xef\x00\x01"      # arbitrary; the interlock forces 02:..:0X
    src = b"\xde\xad\xbe\xef\x00\x02"
    f = dst + src + len(data).to_bytes(2, "big") + data
    return f + b"\x00" * (60 - len(f)) if len(f) < 60 else f

def parse_cert(fr: bytes):
    if len(fr) < 14 + CERT_LEN or fr[6:12] != SERVER: return None
    if int.from_bytes(fr[12:14], "big") != CERT_LEN: return None
    d = fr[14:14 + CERT_LEN]
    return dict(version=int.from_bytes(d[16:20], "big"),
                interlock_id=int.from_bytes(d[20:24], "big"),
                bucket_start=int.from_bytes(d[24:32], "big"),
                num_buckets=int.from_bytes(d[32:36], "big"),
                overall_req=d[36:68], overall_rsp=d[68:100],
                nonce=d[100:116], tau=d[116:148], m=d[16:116], raw=fr)

def cert_tau_ok(c, key):
    return hmac.new(key, c["m"], hashlib.sha256).digest() == c["tau"]

def overall(header: bytes, ciphertext: bytes, datalen: int) -> bytes:
    rec = hashlib.sha256(header + hashlib.sha256(ciphertext).digest()).digest()
    return hashlib.sha256(struct.pack(">H", datalen) + rec).digest()

def is_data_frame(fr):     # port-0 egress that is a forwarded response (not a cert)
    return (len(fr) >= 14 + HDR and fr[6:12] == SERVER and fr[0:6] == CLIENT
            and int.from_bytes(fr[12:14], "big") not in (CERT_LEN,)
            and 16 <= int.from_bytes(fr[12:14], "big") <= 1500)

# ---------------- log -------------------------------------------------------------

def log_path(rid): return os.path.join(LOGDIR, "req_%d.json" % rid)

def next_rid():
    os.makedirs(LOGDIR, exist_ok=True)
    cf = os.path.join(LOGDIR, "counter")
    n = (int(open(cf).read()) + 1) if os.path.exists(cf) else 0
    open(cf, "w").write(str(n))
    return n

def save(entry): open(log_path(entry["rid"]), "w").write(json.dumps(entry, indent=2))
def load(rid): return json.load(open(log_path(rid)))
def hx(b): return binascii.hexlify(b).decode()
def ub(s): return binascii.unhexlify(s)

# ---------------- commands --------------------------------------------------------

def cmd_send(a):
    from scapy.all import AsyncSniffer, conf, Raw
    rid = next_rid()
    header = b"REQ\x00" + rid.to_bytes(4, "big") + b"\x00" * 8      # request ID in [4:8]
    ct = a.text.encode() if a.text is not None else ub(a.hex)       # demo "ciphertext"
    data = header + ct
    assert HDR <= len(data) <= 1500, "data must be 16..1500 bytes"
    fr = frame(data)
    req_overall = overall(header, ct, len(data))

    sniffer = AsyncSniffer(iface=a.iface, lfilter=lambda p: bytes(p)[6:12] == SERVER,
                           store=True)
    sniffer.start(); time.sleep(0.3)
    time.sleep(a.gap_ms / 1000.0)                                   # spacing discipline
    conf.L2socket(iface=a.iface).send(Raw(load=fr))                 # raw 802.3, exact bytes
    print("sent request rid=%d (%dB data)" % (rid, len(data)))
    time.sleep(a.wait_ms / 1000.0)
    frames = [bytes(p) for p in sniffer.stop()]

    certs = [c for c in (parse_cert(f) for f in frames) if c]
    req_cert = next((c for c in certs if c["overall_req"] == req_overall), None)
    resp = next((f for f in frames if is_data_frame(f)
                 and f[14:18] == b"REQ\x00" and f[18:22] == rid.to_bytes(4, "big")), None)
    rsp_cert = None
    rsp_header = rsp_ct = None
    if resp:
        ln = int.from_bytes(resp[12:14], "big")
        rdata = resp[14:14 + ln]
        rsp_header, rsp_ct = rdata[:HDR], rdata[HDR:]
        rsp_overall = overall(rsp_header, rsp_ct, ln)
        rsp_cert = next((c for c in certs if c["overall_rsp"] == rsp_overall), None)

    entry = dict(rid=rid, ts=time.time(),
                 request_data=hx(data), request_overall=hx(req_overall),
                 response_data=hx(rsp_header + rsp_ct) if resp else None,
                 request_cert=hx(req_cert["raw"]) if req_cert else None,
                 response_cert=hx(rsp_cert["raw"]) if rsp_cert else None)
    save(entry)
    _report(entry, a.key)

def _report(entry, key):
    rid = entry["rid"]
    print("rid=%d" % rid)
    for name in ("request", "response"):
        ch = entry[name + "_cert"]
        if not ch:
            print("  %-8s cert: MISSING" % name); continue
        c = parse_cert(ub(ch))
        data = ub(entry[name + "_data"]) if entry[name + "_data"] else b""
        ov = overall(data[:HDR], data[HDR:], len(data)) if data else b""
        which = "overall_req" if name == "request" else "overall_rsp"
        print("  %-8s cert: bucket_start=%d  tau=%s  overall=%s"
              % (name, c["bucket_start"],
                 "PASS" if cert_tau_ok(c, key) else "FAIL",
                 "PASS" if c[which] == ov else "FAIL"))

def cmd_log(a):
    if not os.path.isdir(LOGDIR): print("(no log)"); return
    for fn in sorted(os.listdir(LOGDIR)):
        if fn.startswith("req_") and fn.endswith(".json"):
            e = json.load(open(os.path.join(LOGDIR, fn)))
            print("rid=%-4d  %s  req_cert=%s rsp_cert=%s"
                  % (e["rid"], time.strftime("%H:%M:%S", time.localtime(e["ts"])),
                     "y" if e["request_cert"] else "-", "y" if e["response_cert"] else "-"))

def cmd_show(a):
    e = load(a.rid)
    print(json.dumps(e, indent=2))
    for name in ("request", "response"):
        if e[name + "_data"]:
            d = ub(e[name + "_data"])
            print("  %s plaintext-ish payload: %r" % (name, d[HDR:]))

def cmd_verify(a):
    _report(load(a.rid), a.key)

def cmd_challenge(a):
    """In-band challenge: send a CHALLENGE control packet through the interlock and
    print the STATUS/RESULT control packets the Spark sends back. The proof is generated
    AND verified on the Spark (verify-on-Spark) and never crosses the wire — only these
    small control messages do, so no out-of-band/WiFi channel is needed."""
    from scapy.all import AsyncSniffer, conf, Raw
    e = load(a.rid)
    if not (e["request_cert"] and e["response_cert"]):
        print("rid=%d incomplete (need both certs) — cannot challenge" % a.rid); return
    # body = request_id || req_cert || rsp_cert. The Spark recomputes overall + checks
    # tau against its stored packets, runs prove+verify + plaintext match, and replies.
    body = a.rid.to_bytes(8, "big") + ub(e["request_cert"]) + ub(e["response_cert"])
    header = b"CHL\x00" + a.rid.to_bytes(4, "big") + b"\x00" * 8
    payload = MAGIC + bytes([T_CHALLENGE]) + len(body).to_bytes(2, "big") + body
    fr = frame(header + payload)

    seen = set(); done = {"result": False}

    def on_pkt(p):
        b = bytes(p)
        if b[6:12] != SERVER:
            return
        ln = int.from_bytes(b[12:14], "big")
        pl = b[14 + HDR:14 + ln]
        if pl[:len(MAGIC)] != MAGIC:                     # ignore inference responses + certs
            return
        mt = pl[len(MAGIC)]
        bl = int.from_bytes(pl[len(MAGIC) + 1:len(MAGIC) + 3], "big")
        txt = pl[len(MAGIC) + 3:len(MAGIC) + 3 + bl].decode("utf-8", "replace")
        if (mt, txt) in seen:
            return
        seen.add((mt, txt))
        if mt == T_STATUS:
            print("  [status] " + txt, flush=True)
        elif mt == T_RESULT:
            print("  [RESULT] " + txt, flush=True); done["result"] = True

    sniffer = AsyncSniffer(iface=a.iface, lfilter=lambda p: bytes(p)[6:12] == SERVER,
                           prn=on_pkt, store=False)
    sniffer.start(); time.sleep(0.3)
    time.sleep(a.gap_ms / 1000.0)
    conf.L2socket(iface=a.iface).send(Raw(load=fr))
    print("challenge rid=%d sent in-band; awaiting status/result (timeout %ds)..."
          % (a.rid, a.challenge_timeout))
    deadline = time.time() + a.challenge_timeout
    while time.time() < deadline and not done["result"]:
        time.sleep(0.5)
    sniffer.stop()
    if not done["result"]:
        print("  (timed out waiting for RESULT)")

# ---------------- cli -------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="infcli — interlock port-0 driver")
    p.add_argument("--iface", required=True, help="port-0 Ethernet interface (e.g. en7)")
    p.add_argument("--gap-ms", type=int, default=300, help="min spacing before a send")
    p.add_argument("--wait-ms", type=int, default=2000, help="capture window after a send")
    p.add_argument("--key", default=DEFAULT_KEY.hex(), type=lambda s: ub(s),
                   help="cert HMAC key (hex); demo verifier holds it")
    p.add_argument("--challenge-timeout", type=int, default=900,
                   help="seconds to wait for the RESULT (the proof takes minutes)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("send"); s.set_defaults(fn=cmd_send)
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--text"); g.add_argument("--hex")
    sub.add_parser("log").set_defaults(fn=cmd_log)
    sh = sub.add_parser("show"); sh.add_argument("rid", type=int); sh.set_defaults(fn=cmd_show)
    v = sub.add_parser("verify"); v.add_argument("rid", type=int); v.set_defaults(fn=cmd_verify)
    c = sub.add_parser("challenge"); c.add_argument("rid", type=int); c.set_defaults(fn=cmd_challenge)
    a = p.parse_args()
    a.fn(a)

if __name__ == "__main__":
    main()
