#!/usr/bin/env python3
"""Spark port-1 model-server + in-band ZK control handler for the interlock (#2).

Sits on the prover-compute side (interlock PORT 1 = enP7s7). The interlock forwards
canonical packets here (DST=server 02:..:02, SRC=client 02:..:01). Two kinds:

  * INFERENCE request  -> generate() -> response packet back out port 1
  * ZK CONTROL message -> handle_challenge() -> status/result packets back out port 1

Control vs inference is decided purely by a magic prefix in the PAYLOAD (so a control
message isn't mistaken for an inference request) — the interlock doesn't care, it
forwards + certifies both the same way. Driving the ZK challenge in-band over the
interlock cable avoids needing any out-of-band (WiFi) channel; only small control
messages travel — the proof is generated AND verified here on the Spark and never
crosses the wire.

Wire contract (inference-cli-app.md §2): 802.3 LENGTH frames; packet = [16B header]
[payload]. Control payload = MAGIC(8) || type(1) || body_len(2 BE) || body.

HARD: one packet in flight at a time, spaced (per-packet cert HMAC has no
back-pressure). Inference answers one at a time; control replies are spaced by CTL_GAP.

MODEL AGENT: replace generate(). ZKP AGENT: replace handle_challenge() (parse the body
= request_id + req_cert + rsp_cert, verify the certs, run prove + verify + plaintext
match, and emit real STATUS lines then a RESULT).

run (needs CAP_NET_RAW + CAP_NET_ADMIN for promiscuous mode):
  docker run --rm --network host --cap-add NET_RAW --cap-add NET_ADMIN -v $PWD:/app \
    python:3-slim python3 /app/model_server.py enP7s7
"""
import socket
import struct
import sys
import time

IFACE = sys.argv[1] if len(sys.argv) > 1 else "enP7s7"   # interlock PORT 1 NIC
HDR = 16
SERVER = b"\x02\x00\x00\x00\x00\x02"   # forced DST of a forwarded packet
CLIENT = b"\x02\x00\x00\x00\x00\x01"   # forced SRC of a forwarded packet
ETH_P_ALL = 0x0003

# Promiscuous mode: forwarded packets are addressed to the interlock's forced DST
# (02:..:02), not the host NIC's MAC, so the kernel drops them before AF_PACKET unless
# the NIC is promiscuous. Needs CAP_NET_ADMIN (--cap-add NET_ADMIN).
SOL_PACKET, PACKET_ADD_MEMBERSHIP, PACKET_MR_PROMISC = 263, 1, 1

# In-band ZK control protocol (endpoint convention; the interlock is oblivious).
MAGIC = b"ILKZKCTL"
T_CHALLENGE, T_STATUS, T_RESULT = 1, 2, 3
CTL_GAP = 0.4          # seconds between control replies (each reply gets its own cert)


def generate(req_header: bytes, req_ciphertext: bytes):
    """STUB — model agent replaces. Echo keeps the loop testable with no model."""
    return req_header, req_ciphertext


def handle_challenge(send, req_header: bytes, body: bytes):
    """STUB — ZKP agent replaces. body = request_id || req_cert || rsp_cert.
    Real: verify certs (tau + overall) against the stored packets, run prove + verify
    (minutes) emitting STATUS as it goes, do the plaintext match, then send RESULT
    (PASS/FAIL, U, plaintext-match). Keep sends spaced (each is a cert)."""
    send(T_STATUS, b"challenge received (%dB body)" % len(body))
    send(T_STATUS, b"proving... (stub)")
    send(T_RESULT, b"PASS  U=0.04  plaintext-match=OK  (stub)")


def frame(data: bytes) -> bytes:
    dst = b"\xde\xad\xbe\xef\x00\x01"      # overwritten by the interlock
    src = b"\xde\xad\xbe\xef\x00\x02"
    f = dst + src + len(data).to_bytes(2, "big") + data
    return f + b"\x00" * (60 - len(f)) if len(f) < 60 else f


def ctl_payload(mtype: int, body: bytes) -> bytes:
    return MAGIC + bytes([mtype]) + len(body).to_bytes(2, "big") + body


def set_promisc(sock, iface):
    mreq = struct.pack("iHH8s", socket.if_nametoindex(iface), PACKET_MR_PROMISC, 0, b"")
    sock.setsockopt(SOL_PACKET, PACKET_ADD_MEMBERSHIP, mreq)


def main():
    s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
    s.bind((IFACE, 0))
    set_promisc(s, IFACE)
    print("model_server on %s: requests + in-band ZK control "
          "(DST %s SRC %s)" % (IFACE, SERVER.hex(), CLIENT.hex()), flush=True)
    while True:
        fr = s.recv(2048)
        if len(fr) < 14 + HDR:
            continue
        if fr[0:6] != SERVER or fr[6:12] != CLIENT:        # only canonical forwarded packets
            continue
        lt = int.from_bytes(fr[12:14], "big")
        if lt > 1500 or lt < HDR:
            continue
        data = fr[14:14 + lt]
        header, payload = data[:HDR], data[HDR:]

        if payload[:len(MAGIC)] == MAGIC:                  # ----- ZK control message -----
            mtype = payload[len(MAGIC)]
            blen = int.from_bytes(payload[len(MAGIC) + 1:len(MAGIC) + 3], "big")
            body = payload[len(MAGIC) + 3:len(MAGIC) + 3 + blen]
            if mtype == T_CHALLENGE:
                print("[challenge] body=%dB -> status/result" % len(body), flush=True)

                def send(mt, b, _hdr=header):              # reply on port 1, spaced
                    s.send(frame(_hdr + ctl_payload(mt, b)))
                    time.sleep(CTL_GAP)

                handle_challenge(send, header, body)
            continue                                       # control handled; not inference

        rsp_header, rsp_ct = generate(header, payload)     # ----- inference -----
        s.send(frame(rsp_header + rsp_ct))
        print("[infer] req hdr=%s (%dB) -> rsp (%dB)"
              % (header.hex(), len(payload), len(rsp_ct)), flush=True)


if __name__ == "__main__":
    main()
