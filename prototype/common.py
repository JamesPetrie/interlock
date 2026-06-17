"""Shared infrastructure for the node processes: transport, ports, the shared
secret/params, and (de)serialization. The wire *format* lives in wire.py; this
file is just how nodes find and talk to each other.
"""
import json
import os
import socket
import time

import wire
from transport import send_msg, recv_msg

HOST = "127.0.0.1"
PORT_COMPUTE   = int(os.environ.get("PORT_COMPUTE", 5551))
PORT_INTERLOCK = int(os.environ.get("PORT_INTERLOCK", 5552))
PORT_VERIFIER  = int(os.environ.get("PORT_VERIFIER", 5554))
COMPUTE_HOST   = os.environ.get("COMPUTE_HOST", HOST)   # compute may be remote (e.g. over the FPGA)

KEY   = b"\x00" * 32              # per-request key material (plaintext prototype)
MAC   = wire.H(b"interlock-mac-key")  # shared secret: interlock signs, verifier checks
IID   = 7
NONCE = wire.H(b"nonce")[:16]
N     = 8                         # buckets per certificate (one cert per turn)


def call(host, port, req, tries=60):
    """Send one JSON request, return the JSON reply (retries while a server starts)."""
    for _ in range(tries):
        try:
            s = socket.create_connection((host, port), timeout=10)
            break
        except OSError:
            time.sleep(2)
    else:
        raise ConnectionError(f"cannot reach {host}:{port}")
    send_msg(s, json.dumps(req).encode())
    out = json.loads(recv_msg(s))
    s.close()
    return out


def serve(port, handler, name):
    """Single-threaded request/reply server: one JSON message per connection."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen()
    print(f"[{name}] listening on {port}", flush=True)
    while True:
        c, _ = srv.accept()
        try:
            reply = handler(json.loads(recv_msg(c)))
        except Exception as e:                       # noqa: BLE001 - report to caller
            reply = {"error": f"{type(e).__name__}: {e}"}
        try:
            send_msg(c, json.dumps(reply).encode())
        finally:
            c.close()


def opening_to_json(op):
    """wire.Opening (bytes / list[bytes] / int / None fields) -> JSON-safe dict."""
    out = {}
    for field, v in op._asdict().items():
        if isinstance(v, bytes):
            out[field] = v.hex()
        elif isinstance(v, list):
            out[field] = [x.hex() for x in v]
        else:
            out[field] = v          # int or None
    return out


def json_to_opening(d):
    kw = {}
    for field, v in d.items():
        if v is None or isinstance(v, int):
            kw[field] = v
        elif isinstance(v, list):
            kw[field] = [bytes.fromhex(x) for x in v]
        else:
            kw[field] = bytes.fromhex(v)
    return wire.Opening(**kw)
