#!/usr/bin/env python3
"""infcli — port-0 driver + multi-turn chat frontend for the interlock (#3).

Runs on the client side: the Raspberry Pi on port 1 (RJ45 J30).
It drives one inference packet at a time through the interlock on the client port, captures the
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
# CANON_HDR is the interlock's canonical header (canon_tx builds/strips it and the
# certificate's `overall` covers it); APP_HDR is this app's request-id header, which
# rides inside the canonical payload. The pre-bucket format let these be one 16B field.
CANON_HDR = 64
APP_HDR = 16
HDR = APP_HDR
CERT_LEN = 224                             # 64 reserved || m(128) || tau(32)
DEFAULT_KEY = (99).to_bytes(32, "big")     # gateware ties cert_build .key(256'd99)
LOGDIR = os.path.expanduser("~/.infcli")
MAX_PAYLOAD = 1500                         # 802.3 LENGTH frame data cap (16..1500)

# In-band ZK control protocol (payload-prefix marker; see model_server.py / spec §6,§9).
# The challenge + status/result travel as normal packets through the interlock — no
# out-of-band channel. The proof stays on the Spark (verify-on-Spark); only these small
# control messages cross.
MAGIC = b"ILKZKCTL"
T_CHALLENGE, T_STATUS, T_RESULT = 1, 2, 3

# Capture filter, pushed into the kernel rather than written as a scapy `lfilter`.
# The interlock streams sync frames at 1 kHz, and handing every one of them up to
# Python starves the flywheel thread that has to service the same NIC -- the two
# run on the same pinned, SCHED_FIFO core. A starved flywheel misses the sync
# carrying FIRST_ARR, which is the only acknowledgement the canonical protocol
# has, and a missed acknowledgement is what makes a byte-audit come back
# unexplainable. So drop syncs in the kernel, before Python ever sees them.
#
# The discriminator is the reserved canonical id at DATA offset 8 (frame offset
# 22), NOT the frame length. Length would be wrong: the server's header-only
# keep-alive probes are 64 bytes of canonical DATA too, exactly like a sync, and
# those probes are real forwarded traffic that OUTWARD commits.
SYNC_ID = 1
SNIFF_BPF = ("ether src 02:00:00:00:00:02 and "
             "not (ether[22:4] = 0 and ether[26:4] = %d)" % SYNC_ID)
CERT_WAIT_S = 3.0                          # cap on waiting for a period to close (1 s)

# ---------------- verified wire helpers (identical to the bring-up scripts) -------

def frame(data: bytes) -> bytes:
    dst = b"\xde\xad\xbe\xef\x00\x01"      # arbitrary; the interlock forces 02:..:0X
    src = b"\xde\xad\xbe\xef\x00\x02"
    f = dst + src + len(data).to_bytes(2, "big") + data
    return f + b"\x00" * (60 - len(f)) if len(f) < 60 else f

_PORT = {"p": None}

def canon_port(a):
    """The bucket-timed canonical port, opened on first use. Locking the flywheel and
    bootstrapping the declared-bucket offset take a few seconds, so this is done once
    per process rather than once per request. The client sits on the client port and speaks the
    REQ direction, whose ids must be globally monotonic across runs."""
    if _PORT["p"] is None:
        import canon_tx
        _PORT["p"] = canon_tx.open_port(a.iface, req=True,
                                        accept_dst=CLIENT, accept_src=SERVER)
    return _PORT["p"]


def is_cert_frame(fr: bytes) -> bool:
    """A certificate the interlock minted, as opposed to traffic it forwarded.

    Length alone will not do: a response of exactly 224 canonical bytes (36 token
    ids) is perfectly legal and would be misread as a certificate. What separates
    them is the reserved header -- the interlock zeroes all 64 bytes of it on a
    cert, and no forwarded frame can look like that, since every forwarded frame
    declares a nonzero bucket and a nonzero id."""
    return (len(fr) >= 14 + CERT_LEN and fr[6:12] == SERVER
            and int.from_bytes(fr[12:14], "big") == CERT_LEN
            and fr[14:14 + CANON_HDR] == b"\x00" * CANON_HDR)

def is_sync_frame(fr: bytes) -> bool:
    """The interlock's 1 kHz timeline beacon, identified by the canonical id the
    protocol reserves for it rather than by its length -- keep-alive probes are
    the same size once forwarded."""
    return (len(fr) >= 14 + CANON_HDR
            and int.from_bytes(fr[12:14], "big") == CANON_HDR
            and int.from_bytes(fr[22:30], "big") == SYNC_ID)

def parse_cert(fr: bytes):
    if not is_cert_frame(fr): return None
    d = fr[14:14 + CERT_LEN]
    # m = (VERSION, DEVICE, BKT_START, BKT_NUM, NONCE, INWARD, OUTWARD, CHAIN)
    # behind a 64-byte reserved canonical header -- verification-protocol.md §320.
    return dict(version=int.from_bytes(d[64:68], "big"),
                interlock_id=int.from_bytes(d[68:72], "big"),
                bucket_start=int.from_bytes(d[72:76], "big"),
                num_buckets=int.from_bytes(d[76:80], "big"),
                nonce=d[80:96], overall_req=d[96:128], overall_rsp=d[128:160],
                chain=d[160:192], tau=d[192:224], m=d[64:192], raw=fr)

def cert_tau_ok(c, key):
    return hmac.new(key, c["m"], hashlib.sha256).digest() == c["tau"]

def overall(header: bytes, ciphertext: bytes, datalen: int) -> bytes:
    rec = hashlib.sha256(header + hashlib.sha256(ciphertext).digest()).digest()
    return hashlib.sha256(struct.pack(">H", datalen) + rec).digest()

def is_forwarded(fr):
    """Any frame the interlock forwarded toward this client.

    Responses, in-band control packets, and the server's own header-only keep-alive
    probes alike. That last one is easy to forget and fatal to forget: the probes
    cross the interlock, get accepted, and are committed by OUTWARD exactly like a
    response, so leaving them out of the retained set makes the response-direction
    audit unable to reproduce the root in any period the server was idle in."""
    return (len(fr) >= 14 + CANON_HDR and fr[0:6] == CLIENT and fr[6:12] == SERVER
            and CANON_HDR <= int.from_bytes(fr[12:14], "big") <= 1500
            and not is_cert_frame(fr) and not is_sync_frame(fr))

def is_data_frame(fr):     # a forwarded frame big enough to carry an app header
    return is_forwarded(fr) and int.from_bytes(fr[12:14], "big") >= CANON_HDR + APP_HDR

def certs_in(frames):
    return [c for c in (parse_cert(f) for f in list(frames)) if c]

def cert_for(certs, bkt):
    """The certificate whose period contains `bkt`, if it has been captured.

    A prod-1ms certificate is emitted at the END of the 1000-bucket period it
    commits, so which cert binds a packet is fixed by the packet's declared
    bucket -- there is no ambiguity to resolve, only a wait to survive."""
    if bkt is None:
        return None
    return next((c for c in certs
                 if c["bucket_start"] <= bkt < c["bucket_start"] + c["num_buckets"]), None)

def canon_data(fr):
    """The canonical DATA (header || payload) carried by a captured frame."""
    return fr[14:14 + int.from_bytes(fr[12:14], "big")]

def rsp_by_bucket(frames):
    """Retained response-direction traffic, keyed by declared bucket.

    Everything forwarded counts (see is_forwarded). Certificates do not: they are
    minted by the interlock rather than forwarded through it, so they are not part
    of the traffic they commit."""
    import commit
    by = {}
    for f in list(frames):
        if is_forwarded(f):
            d = canon_data(f)
            by.setdefault(commit.declared_bucket(d), []).append(d)
    return by

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
    from scapy.all import AsyncSniffer
    import commit
    rid = next_rid()
    header = b"REQ\x00" + rid.to_bytes(4, "big") + b"\x00" * 8      # request ID in [4:8]
    data = header + ct
    assert HDR <= len(data) <= MAX_PAYLOAD, "data must be 16..%d bytes (got %d)" % (
        MAX_PAYLOAD, len(data))
    # Open (and bootstrap) the port BEFORE the capture starts, so `cur_bkt` right after
    # sniffer.start() is a true lower bound on the buckets this capture holds in full --
    # which is what makes the response-direction audit below sound rather than hopeful.
    port = canon_port(a)
    frames = []
    sniffer = AsyncSniffer(iface=a.iface, filter=SNIFF_BPF,
                           prn=lambda p: frames.append(bytes(p)), store=False)
    sniffer.start(); time.sleep(0.3)
    capture_from = port.cur_bkt
    time.sleep(a.gap_ms / 1000.0)                                   # spacing discipline
    # Bucket-timed send: the prod-1ms build drops anything that does not arrive in the
    # bucket it declares, so the request goes out through the canonical port, which
    # returns the exact DATA that crossed -- the bytes the certificate's `overall` covers.
    sent = port.send_confirmed(data)
    req_overall = overall(sent[:CANON_HDR], sent[CANON_HDR:], len(sent))
    req_bkt = commit.declared_bucket(sent)
    # Capture until the matching response arrives (generation time varies — model load,
    # token count). Bounded by wait_ms.
    rid_be = rid.to_bytes(4, "big")
    ID_OFF = 14 + CANON_HDR                                         # app header on the wire
    def find_response():
        return next((f for f in list(frames) if is_data_frame(f)
                     and f[ID_OFF:ID_OFF + 4] == b"REQ\x00"
                     and f[ID_OFF + 4:ID_OFF + 8] == rid_be), None)
    deadline = time.time() + a.wait_ms / 1000.0
    resp = None
    while time.time() < deadline:
        resp = find_response()
        if resp:
            break
        time.sleep(0.2)
    rdata = canon_data(resp) if resp else None
    rsp_bkt = commit.declared_bucket(rdata) if resp else None
    # Then wait for the two certificates specifically. A flat linger was the wrong
    # shape and cost a run: a certificate is emitted when its period ENDS, so a packet
    # landing late in its period needs most of a second more, and stopping short of
    # that reported the cert as missing when it was merely not yet sent.
    cert_deadline = time.time() + CERT_WAIT_S
    while time.time() < cert_deadline:
        certs = certs_in(frames)
        if all(cert_for(certs, b) for b in (req_bkt, rsp_bkt) if b is not None):
            # A certificate is the last thing its period puts on the wire, so seeing
            # it means every forwarded frame it commits has already arrived -- but
            # "arrived" is the kernel, not this list. Let the callback drain before
            # stopping, or the audit recomputes a period missing its own tail.
            time.sleep(0.2)
            break
        time.sleep(0.1)
    sniffer.stop()
    certs = certs_in(frames)

    # REQUEST direction. The cert covering our request is the one whose period contains
    # its declared bucket; binding it to our traffic means recomputing that period's
    # root, a statement about ALL our traffic in that second, not just this packet.
    # `audit_search` additionally resolves frames whose acknowledgement never arrived.
    req_cert = cert_for(certs, req_bkt)
    req_audit = req_resolved = None
    if req_cert:
        confirmed, unconfirmed = port.audit_snapshot()
        req_audit, req_resolved = commit.audit_search(
            confirmed, unconfirmed, req_cert, "req")

    rsp_cert = cert_for(certs, rsp_bkt)
    rsp_header = rsp_ct = None
    rsp_audit = None
    if resp:
        rsp_app = rdata[CANON_HDR:]                    # CANON_HDR || app header || ids
        rsp_header, rsp_ct = rsp_app[:APP_HDR], rsp_app[APP_HDR:]
    if rsp_cert:
        # RESPONSE direction. OUTWARD commits everything the interlock forwarded toward
        # this client in that period, and this client is the only receiver on the client port --
        # so our own capture IS the retained set, provided the whole period sits inside
        # the capture window. The right edge is guaranteed by having the certificate at
        # all (it is emitted when the period closes); the left edge is what `capture_from`
        # pins down. Outside that, say n/a rather than audit a set we know is partial.
        if rsp_cert["bucket_start"] >= capture_from:
            rsp_audit = commit.audit(rsp_by_bucket(frames), rsp_cert, "rsp")

    entry = dict(rid=rid, ts=time.time(),
                 request_audit=req_audit, response_audit=rsp_audit,
                 request_resolved=req_resolved or [],
                 request_data=hx(data), request_overall=hx(req_overall),
                 response_data=hx(rsp_header + rsp_ct) if resp else None,
                 request_cert=hx(req_cert["raw"]) if req_cert else None,
                 response_cert=hx(rsp_cert["raw"]) if rsp_cert else None)
    save(entry)
    return entry

def cmd_send(a):
    """`--ids` is the demo path: token ids, encrypted. `--text`/`--hex` stay raw,
    for wire bring-up where the payload is deliberately not a token stream."""
    if getattr(a, "ids", None) is not None:
        ids = [int(v) for v in a.ids.split(",") if v.strip() != ""]
        entry = send_tokens(a, ids)
        print("sent request rid=%d (%d ids, %dB encrypted payload)"
              % (entry["rid"], len(ids), HDR + len(ub(entry["request_data"])) - HDR))
        if entry.get("response_ids") is not None:
            print("response ids: %s" % ",".join(map(str, entry["response_ids"])))
        elif entry.get("crypto_error"):
            print("response NOT decrypted: %s" % entry["crypto_error"])
    else:
        ct = a.text.encode() if a.text is not None else ub(a.hex)   # raw bring-up payload
        entry = send_payload(a, ct)
        print("sent request rid=%d (%dB data)" % (entry["rid"], HDR + len(ct)))
    _report(entry, a.key)


def send_tokens(a, ids):
    """One encrypted certified round trip.

    The payload the interlock certifies is the crypto header (nonce, KEY_COMMIT,
    GCM tag) followed by ciphertext -- no token ever crosses the cable in clear.
    KEY_COMMIT rides in the REQUEST, so the interlock's INWARD digest fixes it
    before the response exists; that is what makes it a *pre*-commitment for the
    ZK proof rather than a value the prover could pick afterwards.

    The per-request key is derived, never transmitted: keymat = HKDF(PSK, nonce),
    so the wire carries only the nonce and both ends land on the same 40 bytes.
    """
    import ilk_crypto as ic
    psk = ic.load_psk()
    payload, ctx = ic.seal_request(psk, ids)
    entry = send_payload(a, payload)
    # Keep the material this rid needs later: `challenge` hands the nonce to the
    # prover (which re-derives the key from its own copy of the PSK -- the key
    # itself never travels), and `show` needs it to decrypt the response.
    entry["nonce"] = hx(ctx["nonce"])
    entry["key_commit"] = hx(ctx["key_commit"])
    entry["ct_in"] = hx(ctx["ct_in"])
    entry["request_ids"] = list(ids)
    entry["encrypted"] = True
    entry["response_ids"] = None
    entry["crypto_error"] = None
    rd = entry.get("response_data")
    if rd:
        rsp_payload = ub(rd)[APP_HDR:]
        try:
            entry["response_ids"], ct_out = ic.open_response(ctx["keymat"], rsp_payload)
            entry["ct_out"] = hx(ct_out)
        except Exception as e:                      # authenticity failure is a result
            entry["crypto_error"] = "%s: %s" % (type(e).__name__, e)
    save(entry)
    return entry

def _report(entry, key):
    rid = entry["rid"]
    print("rid=%d" % rid)
    for name in ("request", "response"):
        ch = entry[name + "_cert"]
        if not ch:
            print("  %-8s cert: NOT CAPTURED" % name); continue
        c = parse_cert(ub(ch))
        # The certificate commits a whole period, so "does this packet bind?" is answered
        # by the byte-audit recorded at send time, not by comparing a per-packet digest.
        audit = entry.get(name + "_audit")
        note = ""
        if name == "request" and entry.get("request_resolved"):
            # Frames the device committed but never acknowledged to us. Worth saying
            # out loud: it means the receive path dropped a sync, not that anything
            # about the binding is weaker.
            note = "  (%d lost ack%s recovered)" % (
                len(entry["request_resolved"]),
                "" if len(entry["request_resolved"]) == 1 else "s")
        print("  %-8s cert: bucket_start=%d  tau=%s  byte-audit=%s%s"
              % (name, c["bucket_start"],
                 "PASS" if cert_tau_ok(c, key) else "FAIL",
                 {True: "PASS", False: "FAIL",
                  None: "n/a (period outside capture)"}[audit], note))

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
    if e.get("request_audit") is False or e.get("response_audit") is False:
        # Worth saying before spending minutes of GPU on it: the proof will still
        # run and still be valid about the model, but the chain from the wire to
        # the proven bytes is already broken, so the panel cannot come back clean.
        on_status("rid=%d WARNING: byte-audit failed (req=%s rsp=%s) — the proof "
                  "cannot bind to traffic this client cannot reproduce"
                  % (rid, e.get("request_audit"), e.get("response_audit")))
    # body = request_id || req_cert_DATA(148) || rsp_cert_DATA(148). The Spark recomputes
    # overall + checks tau against its retained packets, runs prove+verify, then replies.
    rc, sc = parse_cert(ub(e["request_cert"])), parse_cert(ub(e["response_cert"]))
    cdat = lambda c: b"\x00" * CANON_HDR + c["m"] + c["tau"]   # canonical 224B cert DATA
    # Verifier-chosen challenge seed. It selects which (token, layer) a subsampled
    # prover must open, so it MUST be unpredictable to the prover. The old default
    # derived the pick from the request/response bytes -- and the prover chooses the
    # response, so it could grind until the pick landed on a transition it had
    # computed honestly, driving detection from 1/N to ~0. Drawn here, after the
    # response and both certs are already fixed, it cannot be steered. Appended
    # last, so a server that length-checks with >= still parses an older body.
    seed = os.urandom(16)
    body = rid.to_bytes(8, "big") + cdat(rc) + cdat(sc) + seed
    header = b"CHL\x00" + rid.to_bytes(4, "big") + b"\x00" * 8
    payload = MAGIC + bytes([T_CHALLENGE]) + len(body).to_bytes(2, "big") + body

    seen = set(); out = {"result": None}

    def on_pkt(p):
        b = bytes(p)
        if b[6:12] != SERVER:
            return
        ln = int.from_bytes(b[12:14], "big")
        pl = b[14 + CANON_HDR + APP_HDR:14 + ln]         # past canonical + app headers
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

    sniffer = AsyncSniffer(iface=a.iface, filter=SNIFF_BPF, prn=on_pkt, store=False)
    sniffer.start(); time.sleep(0.3)
    time.sleep(a.gap_ms / 1000.0)
    canon_port(a).send_confirmed(header + payload)       # in band, retried until accepted
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
    enc = bool(e.get("encrypted"))
    if enc:
        # On an encrypted run the wire payload is the crypto header plus
        # ciphertext, so hashing it would never match the proof, which is a
        # statement about TOKEN ids. Hash the ids this client holds -- it sent
        # the request and decrypted the response -- and let the ciphertext-to-id
        # step be carried by the in-proof key binding rather than by a hash
        # comparison out here.
        h_req_local = sha(pack_ids(e.get("request_ids") or []))
        h_rsp_local = sha(pack_ids(e.get("response_ids") or [])) if e.get("response_ids") else ""
    else:
        h_req_local = sha(req_data[HDR:])
        h_rsp_local = sha(rsp_data[HDR:]) if rsp_data else ""
    rc = parse_cert(ub(e["request_cert"])) if e["request_cert"] else None
    sc = parse_cert(ub(e["response_cert"])) if e["response_cert"] else None

    def cert_line(c, data, audit):
        """A prod-1ms cert commits a whole 1000-bucket period, so "does this packet
        bind?" is the byte-audit recorded at send time -- never a per-packet digest
        compare, which cannot succeed against an epoch root."""
        if not c:
            return "NOT CAPTURED"
        return "tau %s  byte-audit %s  binds %d B %s payload" % (
            "PASS" if cert_tau_ok(c, key) else "FAIL",
            {True: "PASS", False: "FAIL", None: "n/a"}[audit],
            len(data) - HDR, "ciphertext" if enc else "cleartext")

    r = result or {}
    hreq_p, hrsp_p = r.get("hreq", ""), r.get("hrsp", "")
    # The RESULT is one ~200 B frame, so it carries truncated hashes; the local
    # side holds the full SHA-256. Compare as a prefix, not for equality.
    req_match = bool(hreq_p) and h_req_local.lower().startswith(hreq_p.lower())
    rsp_match = bool(hrsp_p) and h_rsp_local.lower().startswith(hrsp_p.lower())
    bar = "=" * 74
    print("\n" + bar)
    print("  VERIFIED CONVERSATION TURN  (rid=%d)" % rid)
    print(bar)
    print("  Interlock certificate  (checked locally on this machine):")
    print("    request : " + cert_line(rc, req_data, e.get("request_audit")))
    print("    response: " + cert_line(sc, rsp_data, e.get("response_audit")))
    print("  ZK proof  (computed AND verified on the Spark; the proof stayed there):")
    print("    unexplained information  U = %s bits" % r.get("U", "?"))
    print("    proof verify             %s" % r.get("verify", "?"))
    print("    output-binding           %s   (public out_ids == response tokens)"
          % r.get("out_bind", "?"))
    keybind = r.get("keybind", "n/a")
    if enc:
        print("  KEY BINDING  (proven in circuit, on the same tape as the forward pass):")
        print("    KEY_COMMIT (from the certified request, pre-response)")
        print("      %s" % (e.get("key_commit") or "")[:48])
        kb = "OK" if keybind.startswith("OK") else keybind
        print("    Poseidon(key||iv_in||iv_out) == KEY_COMMIT    %s" % kb)
        print("    AES-CTR(key, iv_in,  request tokens ) == certified ct_in    %s" % kb)
        print("    AES-CTR(key, iv_out, response tokens) == certified ct_out   %s" % kb)
        # The weld is what makes the decrypted stream the SAME variables the
        # forward pass scored. Subsampled runs prove the key binding but skip it,
        # and saying "OK" there would claim a link the proof does not contain.
        print("    response tokens welded to the model's own committed tokens  %s"
              % ("n/a (spot-check mode)" if keybind == "OK-NOWELD" else kb))
    print("  INPUT MATCH  (token ids through certificate AND proof):"
          if enc else "  INPUT MATCH  (same bytes through certificate AND proof):")
    print("    request  H(local)=%s.. " % h_req_local[:16])
    print("             H(proof)=%s..  %s" % (hreq_p[:16], "MATCH" if req_match else "MISMATCH"))
    print("    response H(local)=%s.. " % h_rsp_local[:16])
    print("             H(proof)=%s..  %s" % (hrsp_p[:16], "MATCH" if rsp_match else "MISMATCH"))
    verdict = r.get("verdict", "?")
    # A byte-audit that came back FAIL is a broken binding, not a cosmetic detail, so
    # it has to be able to sink the verdict line -- an unaudited period (None) only
    # means this client could not see the whole second, which is weaker but not wrong.
    audits_ok = e.get("request_audit") is not False and e.get("response_audit") is not False
    # An encrypted run must also carry a passing key binding: without it the
    # ciphertext on the cable is tied to nothing, and the hash lines below only
    # say the prover used the ids this client already gave it.
    key_ok = (not enc) or keybind.startswith("OK")
    ok = verdict == "PASS" and req_match and rsp_match and audits_ok and key_ok
    print(bar)
    print("  RESULT: %s   %s" % (
        verdict,
        (("the certified ciphertext opens, under the pre-committed key, to the "
          "proven tokens ✓") if keybind == "OK" else
         ("key binding ✓ — but the tokens are not tied to the sampled forward pass")
         if enc else "the certified input == the proven input ✓") if ok
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
        answer = t.decode(rsp_ids, skip_special_tokens=True)
        cut = answer.find("\n" + a.user_tag)          # trim a hallucinated next turn
        if cut != -1:
            answer = answer[:cut]
        answer = answer.strip()
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
    g.add_argument("--ids", help="comma-separated token ids, sent AES-128-GCM "
                                 "encrypted under the PSK-derived per-request key")
    sub.add_parser("log").set_defaults(fn=cmd_log)
    sh = sub.add_parser("show"); sh.add_argument("rid", type=int); sh.set_defaults(fn=cmd_show)
    v = sub.add_parser("verify"); v.add_argument("rid", type=int); v.set_defaults(fn=cmd_verify)
    c = sub.add_parser("challenge"); c.add_argument("rid", type=int); c.set_defaults(fn=cmd_challenge)
    a = p.parse_args()
    a.fn(a)

if __name__ == "__main__":
    main()
