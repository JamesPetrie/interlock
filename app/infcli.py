#!/usr/bin/env python3
"""infcli — port-0 driver + multi-turn chat frontend for the interlock (#3).

Runs on the client side (MacBook in the demo, or the Spark's second NIC in loopback).
It drives one inference packet at a time through the interlock on port 0, captures the
response and the per-packet certificates, verifies the certs locally, and on demand
triggers a zero-knowledge proof IN BAND over the same cable.

The demo flow is the `chat` command: a multi-turn Llama-2-7b conversation where each
turn's request is the whole conversation so far, re-tokenized into one message. Tokens
never leave this side as text — the payload on the wire is the canonical token-id array
(little-endian uint32, inference-cli-app.md §6.3), which is exactly what the certificate
binds and what the proof runs on. Type `/prove` mid-conversation to kick off the proof on
the Spark; status lines stream back in band and a final panel shows the interlock
certificate and the ZK result side by side, proving they cover the *same* input bytes.

Wire contract + cert format: see inference-cli-app.md. The framing and cert parse/verify
here are the exact code verified on silicon (6/6 tau, 6/6 overall). The only platform-
specific part is the raw-Ethernet transport (scapy/libpcap — macOS has no AF_PACKET);
scapy puts the NIC in promiscuous mode, required to receive the interlock's forced MACs.

  pip install scapy transformers          # transformers only for the tokenizer (no torch)

  sudo python3 infcli.py --iface en7 --model ~/models/llama-2-7b-hf chat
  sudo python3 infcli.py --iface en7 send --text "hello"      # low-level bring-up
  sudo python3 infcli.py --iface en7 challenge 3              # proof on an existing rid

HARD: one packet in flight at a time, spaced (per-packet cert HMAC has no back-pressure
— a flood wedges the interlock). The send loop enforces --gap-ms; the Spark spaces its
control replies too.
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
MAX_PAYLOAD = 1500                         # 802.3 LENGTH frame data cap (16..1500)

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

# ---------------- token-id payload (the binding) ----------------------------------

def pack_ids(ids):     # canonical payload: little-endian uint32, in order (§6.3)
    return b"".join(struct.pack("<I", int(t) & 0xFFFFFFFF) for t in ids)

def unpack_ids(buf):
    n = len(buf) // 4
    return list(struct.unpack("<%dI" % n, buf[:4 * n])) if n else []

_TOK = {"t": None}

def tok(a):
    """Lazy HF tokenizer. Token<->text lives entirely on the client; the proof and the
    certificate only ever see ids, so the tokenizer is not part of the trusted chain."""
    if _TOK["t"] is None:
        from transformers import AutoTokenizer
        _TOK["t"] = AutoTokenizer.from_pretrained(os.path.expanduser(a.model))
    return _TOK["t"]

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
def sha(b): return hashlib.sha256(b).hexdigest()

# ---------------- core send (one certified request/response round) ----------------

def send_payload(a, ct: bytes):
    """Send one request whose payload (ciphertext) is `ct`, capture the response and both
    per-packet certs, verify, persist, and return the log entry. Shared by `send` and the
    `chat` loop. Enforces the one-in-flight + spacing discipline."""
    from scapy.all import AsyncSniffer, conf, Raw
    rid = next_rid()
    header = b"REQ\x00" + rid.to_bytes(4, "big") + b"\x00" * 8      # request ID in [4:8]
    data = header + ct
    assert HDR <= len(data) <= MAX_PAYLOAD, "data must be 16..%d bytes (got %d)" % (
        MAX_PAYLOAD, len(data))
    fr = frame(data)
    req_overall = overall(header, ct, len(data))

    frames = []
    sniffer = AsyncSniffer(iface=a.iface, lfilter=lambda p: bytes(p)[6:12] == SERVER,
                           prn=lambda p: frames.append(bytes(p)), store=False)
    sniffer.start(); time.sleep(0.3)
    time.sleep(a.gap_ms / 1000.0)                                   # spacing discipline
    conf.L2socket(iface=a.iface).send(Raw(load=fr))                 # raw 802.3, exact bytes
    # Capture until the matching response arrives (generation time varies — model load,
    # token count), then linger briefly for its certificate. Bounded by wait_ms.
    rid_be = rid.to_bytes(4, "big")
    def have_response():
        return any(is_data_frame(f) and f[14:18] == b"REQ\x00" and f[18:22] == rid_be
                   for f in frames)
    deadline = time.time() + a.wait_ms / 1000.0
    while time.time() < deadline and not have_response():
        time.sleep(0.2)
    time.sleep(1.5)                                                 # let the rsp cert arrive
    sniffer.stop()

    certs = [c for c in (parse_cert(f) for f in frames) if c]
    req_cert = next((c for c in certs if c["overall_req"] == req_overall), None)
    resp = next((f for f in frames if is_data_frame(f)
                 and f[14:18] == b"REQ\x00" and f[18:22] == rid.to_bytes(4, "big")), None)
    rsp_cert = rsp_header = rsp_ct = None
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
    return entry

def cmd_send(a):
    ct = a.text.encode() if a.text is not None else ub(a.hex)       # raw bring-up payload
    entry = send_payload(a, ct)
    print("sent request rid=%d (%dB data)" % (entry["rid"], HDR + len(ct)))
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

# ---------------- in-band ZK challenge --------------------------------------------

def do_challenge(a, rid, on_status):
    """Send a CHALLENGE control packet in band and stream the Spark's STATUS lines
    (printed via on_status as they arrive). Returns the parsed compact RESULT dict
    (verdict/U/verify/out_bind/hreq/hrsp) or None on timeout. The proof is generated AND
    verified on the Spark; only these small control messages cross the wire."""
    from scapy.all import AsyncSniffer, conf, Raw
    e = load(rid)
    if not (e["request_cert"] and e["response_cert"]):
        on_status("rid=%d incomplete (need both certs) — cannot challenge" % rid)
        return None
    # body = request_id || req_cert_DATA(148) || rsp_cert_DATA(148). The Spark recomputes
    # overall + checks tau against its retained packets, runs prove+verify, then replies.
    rc, sc = parse_cert(ub(e["request_cert"])), parse_cert(ub(e["response_cert"]))
    cdat = lambda c: b"\x00" * 16 + c["m"] + c["tau"]      # canonical 148B cert DATA
    body = rid.to_bytes(8, "big") + cdat(rc) + cdat(sc)
    header = b"CHL\x00" + rid.to_bytes(4, "big") + b"\x00" * 8
    payload = MAGIC + bytes([T_CHALLENGE]) + len(body).to_bytes(2, "big") + body
    fr = frame(header + payload)

    seen = set(); out = {"result": None}

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
            on_status("  [status] " + txt)
        elif mt == T_RESULT:
            d = {}
            for kv in txt.split():
                k, _, val = kv.partition("="); d[k] = val
            out["result"] = d

    sniffer = AsyncSniffer(iface=a.iface, lfilter=lambda p: bytes(p)[6:12] == SERVER,
                           prn=on_pkt, store=False)
    sniffer.start(); time.sleep(0.3)
    time.sleep(a.gap_ms / 1000.0)
    conf.L2socket(iface=a.iface).send(Raw(load=fr))
    on_status("challenge rid=%d sent in band; proving on the Spark (timeout %ds)..."
              % (rid, a.challenge_timeout))
    deadline = time.time() + a.challenge_timeout
    while time.time() < deadline and out["result"] is None:
        time.sleep(0.5)
    sniffer.stop()
    return out["result"]

def combined_panel(a, rid, result):
    """Show the interlock certificate and the ZK proof result together, and prove they
    cover the SAME input: the cert binds the request payload (overall hash), and the
    proof reports H(request)/H(response) — both must equal what this client sent."""
    e = load(rid)
    key = a.key
    req_data = ub(e["request_data"]); rsp_data = ub(e["response_data"] or "")
    h_req_local = sha(req_data[HDR:])
    h_rsp_local = sha(rsp_data[HDR:]) if rsp_data else ""
    rc = parse_cert(ub(e["request_cert"])) if e["request_cert"] else None
    sc = parse_cert(ub(e["response_cert"])) if e["response_cert"] else None

    def cert_line(c, data, which):
        if not c:
            return "MISSING"
        ov = overall(data[:HDR], data[HDR:], len(data))
        return "tau %s  overall %s  binds %d-token payload" % (
            "PASS" if cert_tau_ok(c, key) else "FAIL",
            "PASS" if c[which] == ov else "FAIL", (len(data) - HDR) // 4)

    r = result or {}
    hreq_p, hrsp_p = r.get("hreq", ""), r.get("hrsp", "")
    req_match = bool(hreq_p) and hreq_p.lower() == h_req_local.lower()
    rsp_match = bool(hrsp_p) and hrsp_p.lower() == h_rsp_local.lower()
    bar = "=" * 74
    print("\n" + bar)
    print("  VERIFIED CONVERSATION TURN  (rid=%d)" % rid)
    print(bar)
    print("  Interlock certificate  (checked locally on this machine):")
    print("    request : " + cert_line(rc, req_data, "overall_req"))
    print("    response: " + cert_line(sc, rsp_data, "overall_rsp"))
    print("  ZK proof  (computed AND verified on the Spark; the proof stayed there):")
    print("    unexplained information  U = %s bits" % r.get("U", "?"))
    print("    proof verify             %s" % r.get("verify", "?"))
    print("    output-binding           %s   (public out_ids == response tokens)"
          % r.get("out_bind", "?"))
    print("  INPUT MATCH  (same bytes through certificate AND proof):")
    print("    request  H(local)=%s.. " % h_req_local[:16])
    print("             H(proof)=%s..  %s" % (hreq_p[:16], "MATCH" if req_match else "MISMATCH"))
    print("    response H(local)=%s.. " % h_rsp_local[:16])
    print("             H(proof)=%s..  %s" % (hrsp_p[:16], "MATCH" if rsp_match else "MISMATCH"))
    verdict = r.get("verdict", "?")
    ok = verdict == "PASS" and req_match and rsp_match
    print(bar)
    print("  RESULT: %s   %s" % (
        verdict,
        "the certified input == the proven input ✓" if ok
        else "binding incomplete — inspect above"))
    print(bar + "\n")

def cmd_challenge(a):
    result = do_challenge(a, a.rid, lambda s: print(s, flush=True))
    if result is None:
        print("  (timed out waiting for RESULT)"); return
    combined_panel(a, a.rid, result)

# ---------------- multi-turn chat -------------------------------------------------

def build_request_text(a, history, user):
    """Plain-text concatenation of the running transcript (no chat template). `history`
    is a list of (user, assistant) text pairs already exchanged."""
    parts = []
    if a.system:
        parts.append(a.system)
    for u, b in history:
        parts.append("%s %s\n%s %s" % (a.user_tag, u, a.bot_tag, b))
    parts.append("%s %s\n%s" % (a.user_tag, user, a.bot_tag))
    return ("\n".join(parts)).strip()

def cmd_chat(a):
    t = tok(a)
    history = []          # list of (user_text, assistant_text)
    last_rid = None
    print("multi-turn chat over the interlock (model=%s)." % a.model)
    print("  type a message and press enter; /prove to ZK-verify the last turn;")
    print("  /reset to clear context; /quit to exit.\n")
    while True:
        try:
            user = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); break
        if not user:
            continue
        if user in ("/quit", "/exit"):
            break
        if user == "/reset":
            history = []; last_rid = None; print("(context cleared)\n"); continue
        if user == "/prove":
            if last_rid is None:
                print("(nothing to prove yet — send a message first)\n"); continue
            result = do_challenge(a, last_rid, lambda s: print(s, flush=True))
            if result is None:
                print("  (timed out waiting for RESULT)\n")
            else:
                combined_panel(a, last_rid, result)
            continue

        req_text = build_request_text(a, history, user)
        req_ids = t(req_text, add_special_tokens=True)["input_ids"]
        if HDR + 4 * len(req_ids) > MAX_PAYLOAD:
            print("  ! context is %d tokens, over the %d-token wire limit — /reset to "
                  "continue.\n" % (len(req_ids), (MAX_PAYLOAD - HDR) // 4))
            continue
        entry = send_payload(a, pack_ids(req_ids))
        last_rid = entry["rid"]
        if not entry["response_data"]:
            print("  (no response captured for rid=%d)\n" % last_rid); continue
        rsp_ids = unpack_ids(ub(entry["response_data"])[HDR:])
        answer = t.decode(rsp_ids, skip_special_tokens=True).strip()
        history.append((user, answer))
        rc = "y" if entry["request_cert"] else "-"
        sc = "y" if entry["response_cert"] else "-"
        print("bot> %s" % answer)
        print("     [rid=%d  %d->%d tokens  certs req=%s rsp=%s  /prove to verify]\n"
              % (last_rid, len(req_ids), len(rsp_ids), rc, sc))

# ---------------- misc commands ---------------------------------------------------

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

def cmd_verify(a):
    _report(load(a.rid), a.key)

# ---------------- cli -------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="infcli — interlock port-0 driver + chat")
    p.add_argument("--iface", required=True, help="port-0 Ethernet interface (e.g. en7)")
    p.add_argument("--model", default="~/models/llama-2-7b-hf",
                   help="HF model dir (tokenizer only; token<->text stays on the client)")
    p.add_argument("--gap-ms", type=int, default=300, help="min spacing before a send")
    p.add_argument("--wait-ms", type=int, default=15000,
                   help="max wait for the response (returns as soon as it arrives)")
    p.add_argument("--key", default=DEFAULT_KEY.hex(), type=lambda s: ub(s),
                   help="cert HMAC key (hex); demo verifier holds it")
    p.add_argument("--challenge-timeout", type=int, default=1800,
                   help="seconds to wait for the RESULT (the proof takes minutes)")
    p.add_argument("--system", default="", help="optional plain-text system preamble")
    p.add_argument("--user-tag", default="Question:", help="plain-text turn tag for the user")
    p.add_argument("--bot-tag", default="Answer:", help="plain-text turn tag for the model")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("chat").set_defaults(fn=cmd_chat)
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
