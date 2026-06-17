"""Verifier interlock — in the data path.   [diagram: Verifier interlock]

Holds the interlock logic (validity rules, hashing, per-second certificate) and,
run as a process, forwards each turn to compute and emits the certificate the
prover stores. Verifier-trusted: it holds the shared MAC key. It never originates
or interprets payloads — it forwards and certifies.
"""
import hashlib

from wire import *                       # noqa: F401,F403 - the wire vocabulary
from common import (call, serve, MAC, IID, NONCE, N,
                    COMPUTE_HOST, PORT_COMPUTE, PORT_INTERLOCK)


class Interlock:
    """Processes both streams one packet at a time; emits one HMAC certificate per
    `buckets_per_cert` boundaries. Event-style to mirror the gateware: on_packet /
    on_bucket_boundary / on_second.

    Streaming: instead of accumulating record/bucket-hash lists, it keeps a running
    SHA-256 per direction for the current bucket and another for the whole cert
    window — each packet is folded in and discarded. State is constant (a handful
    of hash contexts + counters), and the certificate is byte-identical to the
    list-based computation (H of a concatenation == streaming update)."""

    def __init__(self, mac_key, interlock_id, s_max=100_000, capacity=100_000,
                 buckets_per_cert=1000):
        self.mac_key, self.interlock_id = mac_key, interlock_id
        self.s_max, self.capacity, self.n = s_max, capacity, buckets_per_cert
        self.bucket = 0                       # monotonic counter (battery-backed with key)
        self.nonce = bytes(16)                # latched verifier nonce
        self.last_in_id = -1                  # inbound comparator, session-wide
        self.last_out_id = -1                 # outbound comparator, resets per bucket
        self.used = {"in": 0, "out": 0}       # bytes in the current bucket
        self.bkt = {"in": hashlib.sha256(), "out": hashlib.sha256()}   # current bucket
        self.all = {"in": hashlib.sha256(), "out": hashlib.sha256()}   # cert window
        self.nb = 0                           # buckets closed this cert window

    def on_nonce(self, nonce):
        self.nonce = nonce  # latch unauthenticated; verifier credits only its own nonces

    def on_packet(self, direction, pkt):
        """Forward (return pkt) or drop (return None). Drops are never hashed."""
        p = parse_packet(direction, pkt)
        last = self.last_in_id if direction == "in" else self.last_out_id
        if p["bucket"] != self.bucket:        # design A: declared bucket must match
            return None                        # the interlock's current bucket, else drop
        if p["length"] != len(p["ciphertext"]) or p["length"] > self.s_max:
            return None
        if p["request_id"] <= last:
            return None
        if self.used[direction] + len(pkt) > self.capacity:
            return None
        if direction == "in":
            self.last_in_id = p["request_id"]
        else:
            self.last_out_id = p["request_id"]
        self.bkt[direction].update(record(direction, pkt))   # fold this packet in
        self.used[direction] += len(pkt)
        return pkt

    def on_bucket_boundary(self):
        """Close the bucket: fold its hash into the cert-window hash, reset per-bucket state."""
        for d in ("in", "out"):
            self.all[d].update(self.bkt[d].digest())
            self.bkt[d], self.used[d] = hashlib.sha256(), 0
        self.last_out_id = -1
        self.bucket += 1
        self.nb += 1

    def on_second(self):
        """After the n-th boundary: emit the certificate, reset the window hashes."""
        assert self.nb == self.n
        m = cert_body_from_roots(self.interlock_id, self.bucket - self.n, self.n,
                                 self.all["in"].digest(), self.all["out"].digest(), self.nonce)
        self.all = {"in": hashlib.sha256(), "out": hashlib.sha256()}
        self.nb = 0
        return m + mac(self.mac_key, m)


if __name__ == "__main__":
    il = Interlock(MAC, IID, buckets_per_cert=N)
    il.on_nonce(NONCE)

    def handler(req):
        if req["op"] != "turn":
            return {"error": "unknown op"}
        req_pkt = bytes.fromhex(req["request_packet"])
        # forward through to compute, get the response packet back. resp_bucket is
        # the bucket the response will be certified in (start+1); compute declares it.
        r = call(COMPUTE_HOST, PORT_COMPUTE, {"op": "workload",
                 "request_packet": req_pkt.hex(), "max_new": req.get("max_new", 48),
                 "resp_bucket": req["resp_bucket"]})
        resp_pkt = bytes.fromhex(r["response_packet"])
        # certify this turn: request in bucket `start`, response in `start+1`
        start = il.bucket
        sched = {0: [("in", req_pkt)], 1: [("out", resp_pkt)]}
        for i in range(N):
            for direction, pkt in sched.get(i, []):
                il.on_packet(direction, pkt)
            il.on_bucket_boundary()
        cert = il.on_second()
        return {"response_packet": resp_pkt.hex(), "certificate": cert.hex(),
                "req_bucket": start, "resp_bucket": start + 1, "text": r["text"]}

    serve(PORT_INTERLOCK, handler, "interlock")
