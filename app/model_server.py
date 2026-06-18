#!/usr/bin/env python3
"""Spark port-1 model-server I/O wrapper for the interlock (#2).

Sits on the prover-compute side (interlock PORT 1 = enP7s7). The interlock forwards
canonical REQUEST packets here (DST=server 02:..:02, SRC=client 02:..:01); we hand the
payload to generate(), then send the RESPONSE back out port 1. The interlock
canonicalizes the response and forwards it to port 0 (the frontend), emitting a
per-packet response certificate on the way.

Wire contract (inference-cli-app.md §2): 802.3 LENGTH frames; packet = [16B header]
[ciphertext]. The interlock forces MACs + regenerates LENGTH/FCS, so we only choose
the DATA; the DST/SRC we set are overwritten.

HARD: one response in flight at a time (the per-packet cert HMAC has no back-pressure;
flooding wedges the interlock). We answer one request at a time, so this holds
naturally — do NOT parallelize sends.

MODEL AGENT: replace generate(). The wire I/O around it is fixed and tested.

run (needs CAP_NET_RAW; on the Spark via docker):
  docker run --rm --network host --cap-add NET_RAW -v /home/claude/fpe:/fpe \
    python:3-slim python3 /fpe/model_server.py enP7s7
"""
import socket
import struct
import sys

IFACE = sys.argv[1] if len(sys.argv) > 1 else "enP7s7"   # interlock PORT 1 NIC
HDR = 16
SERVER = b"\x02\x00\x00\x00\x00\x02"   # forced DST of a forwarded request
CLIENT = b"\x02\x00\x00\x00\x00\x01"   # forced SRC of a forwarded request
ETH_P_ALL = 0x0003

# Forwarded requests are addressed to the interlock's forced DST (02:..:02), NOT the
# host NIC's own MAC — so the kernel drops them before AF_PACKET unless the NIC is in
# PROMISCUOUS mode. Enabling promisc needs CAP_NET_ADMIN (run the container with
# --cap-add NET_ADMIN as well as NET_RAW).
SOL_PACKET = 263
PACKET_ADD_MEMBERSHIP = 1
PACKET_MR_PROMISC = 1


def set_promisc(sock, iface):
    mreq = struct.pack("iHH8s", socket.if_nametoindex(iface), PACKET_MR_PROMISC, 0, b"")
    sock.setsockopt(SOL_PACKET, PACKET_ADD_MEMBERSHIP, mreq)


def generate(req_header: bytes, req_ciphertext: bytes):
    """STUB for bring-up — the model agent replaces this.

    Real version: decrypt(req_ciphertext) -> request token-ids -> HF generate ->
    response token-ids -> encrypt -> rsp_ciphertext, and copy the request ID into
    rsp_header so the interlock-side pairing (proto §2) holds.

    Default echo keeps the response distinguishable yet trivially checkable: same
    request ID (header) and payload, so the frontend sees a well-formed response and
    the loop is testable with no model."""
    rsp_header = req_header                 # echo request ID
    rsp_ciphertext = req_ciphertext         # echo payload
    return rsp_header, rsp_ciphertext


def frame(data: bytes) -> bytes:
    dst = b"\xde\xad\xbe\xef\x00\x01"      # overwritten by the interlock
    src = b"\xde\xad\xbe\xef\x00\x02"
    f = dst + src + len(data).to_bytes(2, "big") + data
    return f + b"\x00" * (60 - len(f)) if len(f) < 60 else f


def main():
    s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
    s.bind((IFACE, 0))
    set_promisc(s, IFACE)
    print("model_server on %s: waiting for forwarded requests "
          "(DST %s SRC %s)" % (IFACE, SERVER.hex(), CLIENT.hex()), flush=True)
    n = 0
    while True:
        fr = s.recv(2048)
        if len(fr) < 14 + HDR:
            continue
        dst, src = fr[0:6], fr[6:12]
        lt = int.from_bytes(fr[12:14], "big")
        if dst != SERVER or src != CLIENT:           # only canonical forwarded requests
            continue
        if lt > 1500 or lt < HDR:                    # 802.3 LENGTH that can hold a header
            continue
        data = fr[14:14 + lt]
        req_header, req_ct = data[:HDR], data[HDR:]
        rsp_header, rsp_ct = generate(req_header, req_ct)
        s.send(frame(rsp_header + rsp_ct))
        n += 1
        print("[%d] req hdr=%s (%dB ct) -> rsp (%dB ct) sent"
              % (n, req_header.hex(), len(req_ct), len(rsp_ct)), flush=True)


if __name__ == "__main__":
    main()
