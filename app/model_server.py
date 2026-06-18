#!/usr/bin/env python3
"""Spark port-1 model-server + in-band ZK control handler for the interlock (#2).

Sits on the prover-compute side (interlock PORT 1 = enP7s7). The interlock forwards
canonical packets here (DST=server 02:..:02, SRC=client 02:..:01). Two kinds, told apart
by a magic PAYLOAD prefix (so control isn't mistaken for an inference request):

  * INFERENCE request  -> generate() -> response packet back out port 1
  * ZK CONTROL message -> handle_challenge() -> status/result packets back out port 1

Driving the ZK challenge in-band over the interlock cable means no out-of-band (WiFi)
channel: only small control messages travel — the proof is generated AND verified here
on the Spark (verify-on-Spark) and never crosses the wire.

Wire contract (inference-cli-app.md §2): 802.3 LENGTH frames; packet = [16B header]
[payload]. Control payload = MAGIC(8) || type(1) || body_len(2 BE) || body.

HARD: one packet in flight at a time, spaced (per-packet cert HMAC has no
back-pressure). Inference answers one at a time; control replies are spaced by CTL_GAP.

  ┌─────────────────────────────────────────────────────────────────────────────┐
  │ MODEL AGENT  -> replace generate().                                           │
  │ ZKP AGENT    -> replace the marked block in handle_challenge() (steps e,f,g). │
  │   Everything else (transport, framing, spacing, cert verify (a)+(b), token    │
  │   extraction (d), the packet store) is done and tested — see HANDOFF-ZKP.md.  │
  └─────────────────────────────────────────────────────────────────────────────┘

run (needs CAP_NET_RAW + CAP_NET_ADMIN for promiscuous mode):
  docker run --rm --network host --cap-add NET_RAW --cap-add NET_ADMIN -v $PWD:/app \
    python:3-slim python3 /app/model_server.py enP7s7
"""
import collections
import hashlib
import hmac
import socket
import struct
import sys
import time

IFACE = sys.argv[1] if len(sys.argv) > 1 else "enP7s7"   # interlock PORT 1 NIC
HDR = 16
SERVER = b"\x02\x00\x00\x00\x00\x02"   # forced DST of a forwarded packet
CLIENT = b"\x02\x00\x00\x00\x00\x01"   # forced SRC of a forwarded packet
ETH_P_ALL = 0x0003
KEY = (2).to_bytes(32, "big")          # cert HMAC key (test build cert_build .key(2))

SOL_PACKET, PACKET_ADD_MEMBERSHIP, PACKET_MR_PROMISC = 263, 1, 1   # promisc (see below)

MAGIC = b"ILKZKCTL"                     # in-band ZK control marker (payload prefix)
T_CHALLENGE, T_STATUS, T_RESULT = 1, 2, 3
CTL_GAP = 0.4                           # seconds between control replies (each is a cert)
CERT_DATA_LEN = 148                     # cert DATA: 16 zero hdr || m(100) || tau(32)
STORE_MAX = 512                         # retained packets for challenge binding


# ----- cert helpers (verified byte-exact on silicon; identical to cert_parse.py) -----

def overall(header: bytes, ciphertext: bytes, datalen: int) -> bytes:
    rec = hashlib.sha256(header + hashlib.sha256(ciphertext).digest()).digest()
    return hashlib.sha256(struct.pack(">H", datalen) + rec).digest()

def overall_of(data: bytes) -> bytes:
    return overall(data[:HDR], data[HDR:], len(data))

def parse_cert_data(d: bytes):
    """Parse the 148-byte cert DATA (no Ethernet header)."""
    return dict(version=int.from_bytes(d[16:20], "big"),
                interlock_id=int.from_bytes(d[20:24], "big"),
                bucket_start=int.from_bytes(d[24:32], "big"),
                overall_req=d[36:68], overall_rsp=d[68:100],
                nonce=d[100:116], tau=d[116:148], m=d[16:116])

def cert_tau_ok(c, key=KEY):
    return hmac.new(key, c["m"], hashlib.sha256).digest() == c["tau"]


# --------------------------- hooks the other agents fill ----------------------------

_MODEL = {"tok": None, "model": None}     # lazily loaded HF Llama-2-7b (greedy decode)


def _load_model():
    if _MODEL["model"] is None:
        import os as _os, torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        path = _os.environ.get("MODEL_DIR", "/models/llama-2-7b-hf")
        print("[generate] loading %s ..." % path, flush=True)
        _MODEL["tok"] = AutoTokenizer.from_pretrained(path)
        _MODEL["model"] = AutoModelForCausalLM.from_pretrained(
            path, torch_dtype=torch.float16).to("cuda").eval()
        print("[generate] model ready", flush=True)
    return _MODEL["tok"], _MODEL["model"]


def generate(req_header: bytes, req_ciphertext: bytes):
    """Greedy (argmax) Llama-2-7b continuation. The payload is the canonical token-id
    array (LE uint32, inference-cli-app.md §6.3): read ids -> greedy-decode K new ids ->
    return them as the response payload, keeping the request header so the client matches
    the response. Token<->text stays on the CLIENT; this side only ever handles ids, so
    this forward, the certificate, and the ZK proof all bind the *same* token ids. Pure
    argmax (do_sample=False) is what makes the proof's unexplained information U ~ 0."""
    import os as _os, struct as _st, torch
    n = len(req_ciphertext) // 4
    in_ids = list(_st.unpack("<%dI" % n, req_ciphertext[:4 * n])) if n else []
    if not in_ids:                                  # nothing to condition on -> echo
        return req_header, req_ciphertext
    tok, model = _load_model()
    k = int(_os.environ.get("MAX_NEW_TOKENS", "64"))
    ids = torch.tensor([in_ids], dtype=torch.long, device="cuda")
    with torch.no_grad():
        out = model.generate(ids, max_new_tokens=k, do_sample=False, num_beams=1,
                             pad_token_id=tok.eos_token_id)
    new_ids = out[0, len(in_ids):].tolist()
    rsp_ct = b"".join(_st.pack("<I", t & 0xFFFFFFFF) for t in new_ids)
    print("[generate] %d in -> %d out ids (greedy)" % (len(in_ids), len(new_ids)), flush=True)
    return req_header, rsp_ct                        # keep req header -> client matches rid


def handle_challenge(send, header, body, store, key=KEY):
    """Handle one in-band CHALLENGE. `send(mtype, bytes)` emits a spaced control reply.

    body = request_id(8) || req_cert_DATA(148) || rsp_cert_DATA(148).

    Steps (a) cert HMAC, (b) bind certs to retained packets, and (d) extract plaintext
    are DONE below. The ZKP AGENT fills (e) plaintext match, (f) proof verify, (g)
    binding — see HANDOFF-ZKP.md and spec §6."""
    if len(body) < 8 + 2 * CERT_DATA_LEN:
        send(T_STATUS, b"bring-up: body carries no certs (%dB)" % len(body))
        send(T_RESULT, b"INCOMPLETE: send request_id||req_cert||rsp_cert to run the chain")
        return
    rid = int.from_bytes(body[0:8], "big")
    req_cert = parse_cert_data(body[8:8 + CERT_DATA_LEN])
    rsp_cert = parse_cert_data(body[8 + CERT_DATA_LEN:8 + 2 * CERT_DATA_LEN])
    send(T_STATUS, b"challenge rid=%d received" % rid)

    # (a) cert authenticity
    if not (cert_tau_ok(req_cert, key) and cert_tau_ok(rsp_cert, key)):
        send(T_RESULT, b"FAIL (a): certificate HMAC invalid"); return
    # (b) bind each cert to a retained packet (recompute overall, look it up)
    req_data = store.get(req_cert["overall_req"])
    rsp_data = store.get(rsp_cert["overall_rsp"])
    if req_data is None or rsp_data is None:
        send(T_RESULT, b"FAIL (b): cert does not bind a retained packet"); return
    send(T_STATUS, b"certs valid + bound to traffic (a,b OK)")
    # (d) plaintext payload (prototype = plaintext token-ids after the 16B header)
    req_tokens = req_data[HDR:]
    rsp_tokens = rsp_data[HDR:]

    # ===================== ZKP AGENT: (e),(f),(g) — wired to infproof =====================
    # req_tokens / rsp_tokens are the cert-verified, traffic-bound plaintext payloads:
    # canonical token-id arrays (LE uint32, inference-cli-app.md §6.3). Prove + Rust-verify
    # ON THE SPARK (verify-on-Spark: the ~100 MB proof never crosses the wire) and bind the
    # proof's PUBLIC output ids to the response. The compute is delegated to infproof's
    # interlock_challenge.py (CHALLENGE_PY), so this file carries no torch/CUDA dependency.
    # The output binding uses the public out_ids (reveal-by-constraint), not a fingerprint.
    import os as _os, subprocess as _sp
    def _ids(buf):                                    # canonical payload -> token ids
        n = len(buf) // 4
        return list(struct.unpack("<%dI" % n, buf[:4 * n])) if n else []
    req_ids, rsp_ids = _ids(req_tokens), _ids(rsp_tokens)
    if not req_ids or not rsp_ids:
        send(T_RESULT, b"FAIL (d): empty token payload"); return
    cmd = _os.environ.get(
        "CHALLENGE_PY",
        "/home/claude/venv-hf/bin/python -u /home/claude/infproof/analysis/interlock_challenge.py"
    ).split() + ["--request", ",".join(map(str, req_ids)),
                 "--response", ",".join(map(str, rsp_ids)),
                 "--t-queries", _os.environ.get("CHALLENGE_TQ", "80")]
    send(T_STATUS, b"proving + verifying on the Spark (minutes; proof stays here)")
    v, last = {"verdict": "FAIL", "U": "NA", "verify": "?", "out_bind": "?",
               "hreq": "", "hrsp": ""}, 0.0
    proc = _sp.Popen(cmd, stdout=_sp.PIPE, stderr=_sp.STDOUT, text=True, bufsize=1)
    for line in proc.stdout:
        line = line.rstrip()
        if line.startswith("CHALLENGE_RESULT"):
            for kv in line.split()[1:]:
                k, _, val = kv.partition("="); v[k] = val
        elif (line[:1] == "[" or "verify]" in line) and time.time() - last > 15:
            send(T_STATUS, ("  " + line[:90]).encode("ascii", "replace")); last = time.time()
    proc.wait()
    # RESULT (spec §6.1): one compact, single-frame key=value line. The client already
    # holds the prompt/completion ids (it sent them); hreq/hrsp let it prove the proof ran
    # on the SAME bytes the certificate bound, without echoing the (growing) id lists.
    out = ("verdict=%s U=%s verify=%s out_bind=%s hreq=%s hrsp=%s" % (
        v["verdict"], v["U"], v["verify"], v["out_bind"], v["hreq"], v["hrsp"])
    ).encode("ascii", "replace")
    send(T_RESULT, out)                               # < 200 B -> always one frame
    # ====================================================================================


# ------------------------------------ transport -------------------------------------

def frame(data: bytes) -> bytes:
    dst = b"\xde\xad\xbe\xef\x00\x01"      # overwritten by the interlock
    src = b"\xde\xad\xbe\xef\x00\x02"
    f = dst + src + len(data).to_bytes(2, "big") + data
    return f + b"\x00" * (60 - len(f)) if len(f) < 60 else f

def ctl_payload(mtype: int, body: bytes) -> bytes:
    return MAGIC + bytes([mtype]) + len(body).to_bytes(2, "big") + body

def set_promisc(sock, iface):
    # Forwarded packets are addressed to the interlock's forced DST (02:..:02), not the
    # host NIC's MAC, so the kernel drops them unless the NIC is promiscuous (CAP_NET_ADMIN).
    mreq = struct.pack("iHH8s", socket.if_nametoindex(iface), PACKET_MR_PROMISC, 0, b"")
    sock.setsockopt(SOL_PACKET, PACKET_ADD_MEMBERSHIP, mreq)


def main():
    s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
    s.bind((IFACE, 0))
    set_promisc(s, IFACE)
    store = collections.OrderedDict()      # overall-hash -> packet DATA (for challenge binding)

    def remember(data):
        store[overall_of(data)] = data
        while len(store) > STORE_MAX:
            store.popitem(last=False)

    # Eager model load (PRELOAD_MODEL=1) so the FIRST request isn't slowed by a ~13 GB
    # weight load while the client's capture window is open. Soft-fail to lazy/echo if
    # torch isn't present (e.g. a cert-only bring-up in a slim container).
    import os as _os
    if _os.environ.get("PRELOAD_MODEL", "0") == "1":
        try:
            _load_model()
        except Exception as ex:                # noqa: BLE001 — keep the server up regardless
            print("[generate] preload skipped (%s); falling back to lazy/echo" % ex, flush=True)

    print("model_server on %s: inference + in-band ZK control "
          "(DST %s SRC %s)" % (IFACE, SERVER.hex(), CLIENT.hex()), flush=True)
    while True:
        fr = s.recv(2048)
        if len(fr) < 14 + HDR or fr[0:6] != SERVER or fr[6:12] != CLIENT:
            continue
        lt = int.from_bytes(fr[12:14], "big")
        if lt > 1500 or lt < HDR:
            continue
        data = fr[14:14 + lt]
        header, payload = data[:HDR], data[HDR:]

        if payload[:len(MAGIC)] == MAGIC:                  # ----- ZK control -----
            mtype = payload[len(MAGIC)]
            blen = int.from_bytes(payload[len(MAGIC) + 1:len(MAGIC) + 3], "big")
            body = payload[len(MAGIC) + 3:len(MAGIC) + 3 + blen]
            if mtype == T_CHALLENGE:
                print("[challenge] body=%dB" % len(body), flush=True)

                def send(mt, b, _hdr=header):              # reply on port 1, spaced
                    s.send(frame(_hdr + ctl_payload(mt, b)))
                    time.sleep(CTL_GAP)

                handle_challenge(send, header, body, store)
            continue

        rsp_header, rsp_ct = generate(header, payload)     # ----- inference -----
        rsp_data = rsp_header + rsp_ct
        remember(data); remember(rsp_data)                 # retain both for challenge binding
        s.send(frame(rsp_data))
        print("[infer] req hdr=%s (%dB) -> rsp (%dB)"
              % (header.hex(), len(payload), len(rsp_ct)), flush=True)


if __name__ == "__main__":
    main()
