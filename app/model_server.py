#!/usr/bin/env python3
"""Spark port-1 model-server + in-band ZK control handler for the interlock (#2).

Sits on the prover-compute side (interlock PORT 0 / J15 = enP7s7). The interlock forwards
canonical packets here (DST=server 02:..:02, SRC=client 02:..:01). Two kinds, told apart
by a magic PAYLOAD prefix (so control isn't mistaken for an inference request):

  * INFERENCE request  -> generate() -> response packet back out the compute port
  * ZK CONTROL message -> handle_challenge() -> status/result packets back out the compute port

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
  │   extraction (d), the packet store) is done and tested.                       │
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
# Two distinct headers, which the pre-bucket format let us conflate (both were 16B):
#   CANON_HDR -- the interlock's canonical header. canon_tx builds it on send and
#                strips it on receive; it is what the certificate's `overall` covers.
#   APP_HDR   -- this app's request-id header, carried inside the canonical payload
#                so a client can match a response to the request it sent.
CANON_HDR = 64
APP_HDR = 16
HDR = CANON_HDR                        # cert `overall` is over canonical header + payload
SERVER = b"\x02\x00\x00\x00\x00\x02"   # forced DST of a forwarded packet
CLIENT = b"\x02\x00\x00\x00\x00\x01"   # forced SRC of a forwarded packet
ETH_P_ALL = 0x0003
KEY = (99).to_bytes(32, "big")         # cert HMAC key (gateware ties cert_build .key(256'd99))

SOL_PACKET, PACKET_ADD_MEMBERSHIP, PACKET_MR_PROMISC = 263, 1, 1   # promisc (see below)

MAGIC = b"ILKZKCTL"                     # in-band ZK control marker (payload prefix)
T_CHALLENGE, T_STATUS, T_RESULT = 1, 2, 3
CTL_GAP = 0.4                           # seconds between control replies (each is a cert)
CERT_DATA_LEN = 224                     # cert DATA: 64 zero hdr || m(128) || tau(32)
STORE_MAX = 4000                        # retained buckets; MUST exceed a cert
                                        # period (1000) or the audit loses buckets


# ----- cert helpers (verified byte-exact on silicon; identical to cert_parse.py) -----

def overall(header: bytes, ciphertext: bytes, datalen: int) -> bytes:
    rec = hashlib.sha256(header + hashlib.sha256(ciphertext).digest()).digest()
    return hashlib.sha256(struct.pack(">H", datalen) + rec).digest()

def overall_of(data: bytes) -> bytes:
    return overall(data[:HDR], data[HDR:], len(data))

def parse_cert_data(d: bytes):
    """Parse the 224-byte cert DATA (no Ethernet header).

    Layout per verification-protocol.md §320 -- a 64-byte reserved canonical
    header, then m = (VERSION, DEVICE, BKT_START, BKT_NUM, NONCE, INWARD,
    OUTWARD, CHAIN), then tau. CHAIN is the previous certificate's tau, which
    chains the cert stream (cert_build.md)."""
    return dict(version=int.from_bytes(d[64:68], "big"),
                interlock_id=int.from_bytes(d[68:72], "big"),
                bucket_start=int.from_bytes(d[72:76], "big"),
                num_buckets=int.from_bytes(d[76:80], "big"),
                nonce=d[80:96], overall_req=d[96:128], overall_rsp=d[128:160],
                chain=d[160:192], tau=d[192:224], m=d[64:192])

def cert_tau_ok(c, key=KEY):
    return hmac.new(key, c["m"], hashlib.sha256).digest() == c["tau"]


# --------------------------- hooks the other agents fill ----------------------------

import json as _json
import os as _os

BACKEND = ("127.0.0.1", int(_os.environ.get("BACKEND_PORT", "9917")))


def backend(req, on_status=None):
    """One request to the GPU back-end (model_backend.py on the host).

    This process runs in a slim container for CAP_NET_RAW + CAP_SYS_NICE; the
    model and the prover live on the host with the validated CUDA venv. Streams
    intermediate {"status": ...} objects to `on_status` and returns the final one."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(float(_os.environ.get("BACKEND_TIMEOUT", "3600")))
    s.connect(BACKEND)
    s.sendall((_json.dumps(req) + "\n").encode())
    buf, last = b"", None
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, _, buf = buf.partition(b"\n")
            if not line.strip():
                continue
            obj = _json.loads(line.decode())
            if "status" in obj and on_status:
                on_status(obj["status"])
            else:
                last = obj
    s.close()
    return last or {}


def generate(req_header: bytes, req_ciphertext: bytes):
    """Greedy (argmax) Llama-2-7b continuation. The payload is the canonical token-id
    array (LE uint32, inference-cli-app.md §6.3): read ids -> greedy-decode K new ids ->
    return them as the response payload, keeping the request header so the client matches
    the response. Token<->text stays on the CLIENT; this side only ever handles ids, so
    this forward, the certificate, and the ZK proof all bind the *same* token ids. Pure
    argmax (do_sample=False) is what makes the proof's unexplained information U ~ 0."""
    import struct as _st
    import ilk_crypto as ic

    if ic.is_encrypted(req_ciphertext):
        # Encrypted path. The key is DERIVED from the PSK and the nonce carried in
        # the request; it never crosses the cable. open_request also authenticates
        # (GCM tag) -- CTR is malleable, so an unauthenticated decrypt would let
        # anyone on the wire flip ciphertext bits and choose the ids this model
        # generates from.
        try:
            in_ids, ctx = ic.open_request(ic.load_psk(), req_ciphertext)
        except Exception as e:
            print("[generate] rejecting payload: %s: %s" % (type(e).__name__, e), flush=True)
            return req_header, b""
        if not in_ids:
            return req_header, req_ciphertext
        new_ids = backend({"op": "generate", "ids": in_ids}).get("ids", [])
        rsp_payload, _ct = ic.seal_response(ctx["keymat"], new_ids)
        print("[generate] %d in -> %d out ids (greedy, AES-128-GCM)"
              % (len(in_ids), len(new_ids)), flush=True)
        return req_header, rsp_payload

    # Cleartext path, kept for wire bring-up (`infcli send --hex/--text`), where
    # the payload is deliberately not a token stream.
    n = len(req_ciphertext) // 4
    in_ids = list(_st.unpack("<%dI" % n, req_ciphertext[:4 * n])) if n else []
    if not in_ids:                                  # nothing to condition on -> echo
        return req_header, req_ciphertext
    new_ids = backend({"op": "generate", "ids": in_ids}).get("ids", [])
    rsp_ct = b"".join(_st.pack("<I", t & 0xFFFFFFFF) for t in new_ids)
    print("[generate] %d in -> %d out ids (greedy, CLEARTEXT)"
          % (len(in_ids), len(new_ids)), flush=True)
    return req_header, rsp_ct                        # keep req header -> client matches rid


# Duplicate-delivery guard AND result cache: rid -> the RESULT bytes once one
# was sent, None while a challenge is still running. It used to be a bare set,
# which ate retries whole: a RESULT frame lost past all of send_confirmed's
# retries left the client timing out and the rid unprovable forever, and a
# second `/prove` on the same chat turn was a guaranteed silent timeout. Now a
# repeat of an answered rid gets its RESULT again.
_HANDLED = {}


def handle_challenge(send_raw, header, body, store, key=KEY):
    """Handle one in-band CHALLENGE. `send(mtype, bytes)` emits a spaced control reply.

    body = request_id(8) || req_cert_DATA(148) || rsp_cert_DATA(148).

    Steps (a) cert HMAC, (b) bind certs to retained packets, and (d) extract plaintext
    are DONE below. The ZKP AGENT fills (e) plaintext match, (f) proof verify, (g)
    binding — see spec §6."""
    if len(body) < 8 + 2 * CERT_DATA_LEN:
        send_raw(T_STATUS, b"bring-up: body carries no certs (%dB)" % len(body))
        send_raw(T_RESULT, b"INCOMPLETE: send request_id||req_cert||rsp_cert to run the chain")
        return
    rid = int.from_bytes(body[0:8], "big")
    # A repeat of a challenge: send_confirmed retries whenever it cannot confirm
    # delivery, and a frame the device DID accept but whose FIRST_ARR we missed
    # arrives twice -- which must not start a second multi-minute proof. But a
    # repeat can also mean the RESULT itself was lost, so an ANSWERED rid gets
    # its cached RESULT again rather than silence; only a rid still mid-proof is
    # ignored outright.
    if rid in _HANDLED:
        prev = _HANDLED[rid]
        if prev is None:
            print("[challenge] rid=%d still running; ignoring duplicate" % rid, flush=True)
        else:
            print("[challenge] rid=%d already answered; re-sending its RESULT" % rid,
                  flush=True)
            send_raw(T_RESULT, prev)
        return
    _HANDLED[rid] = None
    while len(_HANDLED) > 256:                   # oldest rids are dead conversations
        _HANDLED.pop(next(iter(_HANDLED)))

    def send(mt, b):
        # Every RESULT that leaves here is cached under its rid, so the whole
        # body below can keep saying `send(T_RESULT, ...)` and forget about it.
        if mt == T_RESULT:
            _HANDLED[rid] = bytes(b)
        send_raw(mt, b)
    req_cert = parse_cert_data(body[8:8 + CERT_DATA_LEN])
    rsp_cert = parse_cert_data(body[8 + CERT_DATA_LEN:8 + 2 * CERT_DATA_LEN])
    # Optional trailing verifier seed (see infcli.do_challenge). Absent from older
    # clients, in which case the prover falls back to its own derivation and the
    # subsampled pick is grindable -- so say which one happened.
    _seed = body[8 + 2 * CERT_DATA_LEN:8 + 2 * CERT_DATA_LEN + 16]
    print("[challenge] rid=%d verifier seed: %s" % (
        rid, _seed.hex() if len(_seed) == 16 else "ABSENT (prover-derived pick)"),
        flush=True)
    send(T_STATUS, b"challenge rid=%d received" % rid)

    # (a) cert authenticity
    if not (cert_tau_ok(req_cert, key) and cert_tau_ok(rsp_cert, key)):
        send(T_RESULT, b"FAIL (a): certificate HMAC invalid"); return
    # (b) byte-audit: recompute each certificate's digest from retained traffic.
    # A prod-1ms cert commits a whole 1000-bucket period, so this proves the retained
    # set IS that period's traffic -- strictly more than the old per-packet lookup.
    import commit
    if not commit.audit(store["req"], req_cert, "req"):
        send(T_RESULT, b"FAIL (b): retained requests do not reproduce the certificate"); return
    # The response side is our own send log, and a frame lands there only once the
    # device acknowledges it -- an acknowledgement that a starved receive path can
    # miss, leaving a committed frame out of the retained set and failing an audit
    # that should pass. audit_search asks the certificate which frames it actually
    # covered; the root is still what gets checked, so nothing is taken on trust.
    # Snapshot first: the flywheel thread moves frames between these two logs.
    confirmed, unconfirmed = store["port"].audit_snapshot()
    rsp_ok, resolved = commit.audit_search(confirmed, unconfirmed, rsp_cert, "rsp")
    if not rsp_ok:
        send(T_RESULT, b"FAIL (b): retained responses do not reproduce the certificate"); return
    for b in resolved or []:
        # The certificate just told us these were committed after all, so fold them
        # into the retained set -- otherwise the rid we are challenged on could sit in
        # one of them and _find below would report it as never sent.
        store["rsp"].setdefault(b, []).extend(unconfirmed[b])
    if resolved:
        print("[challenge] rid=%d: %d response frame(s) committed without a "
              "FIRST_ARR we saw (buckets %s)" % (rid, len(resolved), resolved), flush=True)
    # The challenged packets are the ones carrying this rid, inside the audited period.
    def _find(direction, cert, tag):
        for b in range(cert["bucket_start"], cert["bucket_start"] + cert["num_buckets"]):
            for d in store[direction].get(b, []):
                app = d[CANON_HDR:CANON_HDR + APP_HDR]
                if app[:4] == tag and int.from_bytes(app[4:8], "big") == rid:
                    return d
        return None
    req_data = _find("req", req_cert, b"REQ\x00")
    rsp_data = _find("rsp", rsp_cert, b"REQ\x00")
    if req_data is None or rsp_data is None:
        send(T_RESULT, b"FAIL (b): no retained packet for rid=%d in the audited period" % rid)
        return
    send(T_STATUS, b"certs valid + traffic byte-audited (a,b OK)")
    # (d) plaintext payload: token ids sit after BOTH headers -- the canonical one
    # the interlock stamped and this app's request-id header.
    req_tokens = req_data[CANON_HDR + APP_HDR:]
    rsp_tokens = rsp_data[CANON_HDR + APP_HDR:]

    # ===================== ZKP AGENT: (e),(f),(g) — wired to VerInf =====================
    # req_tokens / rsp_tokens are the cert-verified, traffic-bound plaintext payloads:
    # canonical token-id arrays (LE uint32, inference-cli-app.md §6.3). Prove + Rust-verify
    # ON THE SPARK (verify-on-Spark: the ~100 MB proof never crosses the wire) and bind the
    # proof's PUBLIC output ids to the response. The compute is delegated to VerInf's
    # interlock_challenge.py (CHALLENGE_PY), so this file carries no torch/CUDA dependency.
    # The output binding uses the public out_ids (reveal-by-constraint), not a fingerprint.
    def _ids(buf):                                    # canonical payload -> token ids
        n = len(buf) // 4
        return list(struct.unpack("<%dI" % n, buf[:4 * n])) if n else []

    # These two buffers came out of the RETAINED, byte-audited traffic above --
    # they are the bytes the certificate commits, not a copy the client sent us
    # separately. Decrypting them here is what lets the proof bind the key to the
    # certified ciphertext rather than to something re-supplied out of band.
    import ilk_crypto as ic
    crypto = None
    if ic.is_encrypted(req_tokens):
        try:
            req_ids, cctx = ic.open_request(ic.load_psk(), req_tokens)
            rsp_ids, ct_out = ic.open_response(cctx["keymat"], rsp_tokens)
        except Exception as e:
            send(T_RESULT, ("FAIL (d): payload authentication failed: %s: %s"
                            % (type(e).__name__, e)).encode("ascii", "replace"))
            return
        crypto = {"nonce": cctx["nonce"].hex(),
                  "key_commit": cctx["key_commit"].hex(),
                  "ct_in": cctx["ct_in"].hex(), "ct_out": ct_out.hex()}
        send(T_STATUS, b"payloads decrypted + GCM-authenticated (AES-128-GCM)")
    else:
        req_ids, rsp_ids = _ids(req_tokens), _ids(rsp_tokens)
    if not req_ids or not rsp_ids:
        send(T_RESULT, b"FAIL (d): empty token payload"); return

    # ---- beat 11: the dishonest datacenter ---------------------------------
    # One-shot flag dropped by demo_server (this directory is the container's
    # /app, so host and container see the same file). When it is present the
    # prover is handed a DIFFERENT response than the one the certifier
    # fingerprinted -- the datacenter claiming an output it did not produce.
    #
    # This fails at full strength even in subsample mode, and that is the whole
    # point of using it for the demo: the failure is in B1_out, which is not
    # sampled. AES-CTR(key, iv_out, rsp_ids) is pinned to the certified ct_out,
    # so a single changed token breaks the pin every time. The subsampled body
    # (1 token-layer of ~460) is not what catches this and must not be credited
    # with it. Removed as soon as it is read so it can never latch on.
    _tamper = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".tamper")
    if _os.path.exists(_tamper):
        try:
            _os.unlink(_tamper)
        except OSError:
            pass
        _orig = list(rsp_ids)
        rsp_ids = list(rsp_ids)
        rsp_ids[-1] = (rsp_ids[-1] + 1) % 32000
        print("[challenge] TAMPER: proving response %s instead of the certified %s"
              % (rsp_ids[-3:], _orig[-3:]), flush=True)
        send(T_STATUS, b"TAMPERED: proving an output the certifier did not fingerprint")

    send(T_STATUS, b"proving + verifying on the Spark (minutes; proof stays here)")
    # Cadence of streamed progress STATUS packets. Each control reply traverses the
    # interlock and is certified; raise CHALLENGE_STATUS_SECS to thin the in-band control
    # traffic if the bridge proves sensitive to it (default 15 s — already well spaced).
    _status_secs = float(_os.environ.get("CHALLENGE_STATUS_SECS", "15"))
    v = {"verdict": "FAIL", "U": "NA", "verify": "?", "out_bind": "?",
         "hreq": "", "hrsp": "", "keybind": "n/a"}
    last = [0.0]

    def on_status(line):
        # Progress lines are decoration. A proof runs for minutes, so the odds of hitting
        # a transient placement failure somewhere in that window are real -- and losing
        # one status packet must never abort a proof that is already running, which is
        # exactly what an escaping send error did before. Best-effort, always.
        if time.time() - last[0] > _status_secs:
            last[0] = time.time()
            try:
                send(T_STATUS, ("  " + line[:90]).encode("ascii", "replace"))
            except Exception as e:
                print("[challenge] status send dropped: %s: %s"
                      % (type(e).__name__, e), flush=True)

    # `crypto` carries the PUBLIC binding inputs (KEY_COMMIT + both ciphertexts)
    # and the nonce the prover re-derives the key from with its own copy of the
    # PSK. The key itself is never sent, not even over loopback.
    out = backend({"op": "challenge", "req": req_ids, "rsp": rsp_ids,
                   "crypto": crypto,
                   "seed": _seed.hex() if len(_seed) == 16 else None,
                   "tq": _os.environ.get("CHALLENGE_TQ", "80")}, on_status=on_status)
    v.update(out.get("result") or {})
    # RESULT (spec §6.1): one compact, single-frame key=value line. The client already
    # holds the prompt/completion ids (it sent them); hreq/hrsp let it prove the proof ran
    # on the SAME bytes the certificate bound, without echoing the (growing) id lists.
    out = ("verdict=%s U=%s verify=%s out_bind=%s hreq=%s hrsp=%s keybind=%s" % (
        v["verdict"], v["U"], v["verify"], v["out_bind"], v["hreq"], v["hrsp"],
        v.get("keybind", "n/a"))
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
    import canon_tx, commit
    # Port 1 faces the quarantined compute node and speaks the RSP direction, whose
    # ids reset per bucket (req=False). Filtering on the interlock's forced MACs
    # keeps our own transmissions out of the receive path.
    port = canon_tx.open_port(IFACE, req=False, accept_dst=SERVER, accept_src=CLIENT)
    # Retained traffic, indexed by the bucket each packet declares and split by
    # direction: a certificate commits the two directions separately (INWARD covers
    # what we received, OUTWARD what we sent), and the byte-audit recomputes each
    # from its own side's traffic.
    # The byte-audit recomputes a whole certificate period, so the retained set must be
    # ALL the traffic of that period -- not just the inference packets. On the response
    # side that is exactly canon_tx's sent_log (every frame the device confirmed
    # accepting, bootstrap probes and control replies included), so share that object
    # rather than keeping a second, partial copy.
    # "port" is carried so the challenge handler can take a consistent snapshot of the
    # two send logs: a frame is only in sent_log once the device acknowledges it, and
    # a lost sync is indistinguishable from a rejection here, so the audit has to be
    # able to ask the certificate which it was (see commit.audit_search).
    store = {"req": collections.OrderedDict(), "rsp": port.sent_log, "port": port}

    def remember(data, direction):
        s = store[direction]
        s.setdefault(commit.declared_bucket(data), []).append(data)
        while len(s) > STORE_MAX:
            s.popitem(last=False)

    # Check the GPU back-end is reachable before a client is waiting on us. The model
    # load happens over there; this only confirms the socket answers.
    try:
        probe = socket.create_connection(BACKEND, timeout=5)
        probe.close()
        print("[backend] reachable at %s:%d" % BACKEND, flush=True)
    except OSError as ex:
        print("[backend] WARNING not reachable at %s:%d (%s) -- start model_backend.py "
              "on the host; requests will fail until it is up" % (BACKEND + (ex,)), flush=True)

    print("model_server on %s: inference + in-band ZK control "
          "(DST %s SRC %s)" % (IFACE, SERVER.hex(), CLIENT.hex()), flush=True)

    def sturdy(fn, *a):
        """One retry after recover(). A board power-cycled mid-session voids the
        port's bootstrap; by the time anything sends again the sync stream is
        usually back, and recover() relocks and re-bootstraps in place. Without
        this the port stays dead until the container is restarted -- observed
        2026-08-24, when a restarted FPGA turned every send into
        "send() before bootstrap()" while recv kept working."""
        try:
            return fn(*a)
        except RuntimeError as e:
            if "bootstrap" not in str(e):
                raise
            print("[wire] %s -- recovering the port in place" % e, flush=True)
            try:
                port.recover()
            except RuntimeError as e2:
                if "not being fed" in str(e2):
                    # Sync frames can be on the wire while THIS process's socket
                    # is blind: an AF_PACKET socket bound during a boot-time NIC
                    # flap never receives again (observed 2026-08-27 -- tcpdump
                    # saw the 1 kHz stream, the container did not). No
                    # in-process recovery can rebind it, so exit and let the
                    # restart policy start clean. A genuinely quiet board makes
                    # this a backoff restart loop, which bringup.sh already
                    # blesses as self-healing once the interlock is powered.
                    print("[wire] port starved of sync (%s) -- exiting for a "
                          "clean restart" % e2, flush=True)
                    sys.exit(3)
                raise
            return fn(*a)

    # A blind socket is SILENT, not broken: an AF_PACKET socket bound across a
    # NIC flap or a board power-cycle can sit forever seeing nothing while the
    # host watches the 1 kHz stream arrive (observed 2026-08-27, twice: the
    # request was certified by the FPGA and this loop never heard it). The
    # send-side heal cannot fire -- nothing arrives, so nothing is ever sent.
    # So the receive side checks the flywheel pulse: sync edges stamp
    # port.last_edge_ns ~1000x a second, and a long silence means blind socket
    # or quiet board -- either way, exit and let the restart policy rebind.
    # (On a genuinely dead board this is the same blessed backoff loop as the
    # send-side exit.)
    IDLE_SYNC_S = 30.0
    while True:
        data = port.recv(timeout=5)              # canonical DATA: CANON_HDR || app bytes
        if data is None:
            age_s = (time.monotonic_ns() - port.last_edge_ns) / 1e9
            if age_s > IDLE_SYNC_S:
                print("[wire] no sync edge in %.0fs -- blind socket or quiet "
                      "board; exiting for a clean restart" % age_s, flush=True)
                sys.exit(3)
            continue
        # Retain first: the audit must account for every packet the device committed
        # in the period, including bring-up probes that carry no app header.
        remember(data, "req")
        if len(data) < CANON_HDR + APP_HDR:
            continue
        app = data[CANON_HDR:]
        header, payload = app[:APP_HDR], app[APP_HDR:]

        if payload[:len(MAGIC)] == MAGIC:                  # ----- ZK control -----
            mtype = payload[len(MAGIC)]
            blen = int.from_bytes(payload[len(MAGIC) + 1:len(MAGIC) + 3], "big")
            body = payload[len(MAGIC) + 3:len(MAGIC) + 3 + blen]
            if mtype == T_CHALLENGE:
                print("[challenge] body=%dB" % len(body), flush=True)

                def send(mt, b, _hdr=header):              # reply on port 1, spaced
                    sturdy(port.send_confirmed, _hdr + ctl_payload(mt, b))
                    time.sleep(CTL_GAP)

                try:
                    handle_challenge(send, header, body, store)
                except Exception as e:                 # never take the server down
                    print("[challenge] failed: %s: %s" % (type(e).__name__, e), flush=True)
                    # A crashed challenge must stay retryable: leaving its
                    # in-progress marker would eat every future attempt at this
                    # rid in silence. A rid that already answered keeps its cache.
                    if len(body) >= 8:
                        _rid = int.from_bytes(body[0:8], "big")
                        if _HANDLED.get(_rid) is None:
                            _HANDLED.pop(_rid, None)
            continue

        try:                                               # ----- inference -----
            rsp_header, rsp_ct = generate(header, payload)
            sturdy(port.send_confirmed, rsp_header + rsp_ct)  # DATA the device accepted
            # rsp side is retained by canon_tx itself (store["rsp"] IS port.sent_log)
            print("[infer] req hdr=%s (%dB) -> rsp (%dB)"
                  % (header.hex(), len(payload), len(rsp_ct)), flush=True)
        except Exception as e:
            print("[infer] failed: %s: %s" % (type(e).__name__, e), flush=True)


if __name__ == "__main__":
    main()
