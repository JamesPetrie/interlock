"""Prover frontend — the log + challenge openings, and the interactive chat CLI.
   [diagram: Prover frontend]

Originates requests, stores the certified packets + certificates, and builds
challenge openings from them. As a process it's the chat client: connects to the
interlock for the data path and to the verifier for challenges.
"""
from wire import *                       # noqa: F401,F403 - the wire vocabulary
from common import (call, KEY, IID, NONCE, N, HOST,
                    PORT_INTERLOCK, PORT_VERIFIER, opening_to_json)


class Frontend:
    """Stores forwarded packets (with their bucket) + certificates; recomputes
    derived hashes from the log on demand to build a challenge opening."""

    def __init__(self, interlock_id, buckets_per_cert=1000):
        self.interlock_id, self.n = interlock_id, buckets_per_cert
        self.log = []     # (bucket, direction, packet_bytes), forwarded packets only
        self.certs = {}   # bucket_start -> certificate bytes

    def log_packet(self, direction, pkt):
        """Bucket is taken from the packet header (design A): the log is
        self-describing — no side-channel bucket assignment to get wrong, and it
        groups byte-identically to what the interlock committed."""
        self.log.append((parse_packet(direction, pkt)["bucket"], direction, pkt))

    def log_certificate(self, cert):
        self.certs[parse_certificate(cert)["bucket_start"]] = cert

    def audit_certificate(self, cert, expected_nonce):
        """The certificate body is a deterministic function of the log: byte-check it."""
        start = parse_certificate(cert)["bucket_start"]
        expect = cert_body(self.interlock_id, start,
                           self.second_hashes(start, "in"),
                           self.second_hashes(start, "out"), expected_nonce)
        return cert[:-TAG] == expect

    def bucket_packets(self, bucket, direction):
        return [p for b, d, p in self.log if b == bucket and d == direction]

    def second_hashes(self, start, direction):
        return [bucket_hash([record(direction, p) for p in self.bucket_packets(b, direction)])
                for b in range(start, start + self.n)]

    def _side(self, bucket, direction):
        """-> (certificate, that second's bucket hashes, the bucket's records)."""
        start = bucket - bucket % self.n
        return (self.certs[start], self.second_hashes(start, direction),
                [record(direction, p) for p in self.bucket_packets(bucket, direction)])

    def open_challenge(self, y, x):
        """Opening for byte x of output bucket y."""
        cert_out, hashes_out, records_out = self._side(y, "out")
        hit = locate("out", records_out, x)
        if hit is None:
            return Opening(y, cert_out, hashes_out, records_out)
        pkt = self.bucket_packets(y, "out")[hit]
        rid = parse_packet("out", pkt)["request_id"]
        w, in_pkt = next((b, p) for b, d, p in self.log
                         if d == "in" and parse_packet("in", p)["request_id"] == rid)
        cert_in, hashes_in, records_in = self._side(w, "in")
        return Opening(y, cert_out, hashes_out, records_out,
                       header_out=pkt[:HDR["out"]], h1_out=H(pkt[HDR["out"]:]),
                       w=w, cert_in=cert_in, hashes_in=hashes_in, records_in=records_in,
                       header_in=in_pkt[:HDR["in"]], h1_in=H(in_pkt[HDR["in"]:]))


# --- chat client (the customer + prover frontend, interactive) --------------

fe = Frontend(IID, buckets_per_cert=N)
history, turns, state = [], {}, {"rid": 0, "next_start": 0}


def prompt_for(msg):
    ctx = "".join(f"Q: {u}\nA: {a}\n" for u, a in history)
    return ctx + f"Q: {msg}\nA:"


def say(msg):
    state["rid"] += 1
    rid = state["rid"]
    # Design A: declare the bucket in the header. The prototype is synchronous and
    # the interlock advances by N buckets per turn, so the frontend predicts the
    # interlock's current bucket with a mirror counter (in production this comes
    # from the interlock's tick beacon). req lands in `start`, resp in `start+1`.
    start = state["next_start"]
    req_bucket, resp_bucket = start, start + 1
    req_pkt = input_packet(rid, req_bucket, KEY, prompt_for(msg).encode())
    r = call(HOST, PORT_INTERLOCK, {"op": "turn", "request_packet": req_pkt.hex(),
                                    "resp_bucket": resp_bucket})
    resp_pkt = bytes.fromhex(r["response_packet"])
    # the interlock's actual buckets must equal what we declared, else it dropped them
    assert r["req_bucket"] == req_bucket and r["resp_bucket"] == resp_bucket, \
        "bucket prediction desynced from the interlock"
    fe.log_packet("in", req_pkt)
    fe.log_packet("out", resp_pkt)
    cert = bytes.fromhex(r["certificate"])
    fe.log_certificate(cert)
    assert fe.audit_certificate(cert, NONCE), "certificate is not a function of the log"
    text = r["text"].split("\nQ:")[0].strip()
    history.append((msg, text))
    turns[rid] = resp_bucket
    state["next_start"] = start + N
    return text, rid


def challenge(rid):
    return call(HOST, PORT_VERIFIER,
                {"op": "challenge", "opening": opening_to_json(fe.open_challenge(turns[rid], 0))})


def show_cert(rid):
    """Print the certificate that commits this packet, plus the packet's record."""
    op = fe.open_challenge(turns[rid], 0)
    c = parse_certificate(op.cert_out)
    rec = unpack(RECORD, op.records_out[locate("out", op.records_out, 0)])
    print(f"  === certificate committing packet #{rid} (response bucket {turns[rid]}) ===")
    print(f"  version      {c['version'].decode(errors='replace')}")
    print(f"  interlock_id {c['interlock_id']}")
    print(f"  bucket_start {c['bucket_start']}   num_buckets {c['num_buckets']}")
    print(f"  overall_out  {c['overall_out'].hex()}   (commits all output buckets this cert)")
    print(f"  overall_in   {c['overall_in'].hex()}")
    print(f"  nonce        {c['nonce'].hex()}")
    print(f"  hmac_tag     {c['tag'].hex()}")
    print(f"  -- this packet's committed record --")
    print(f"  request_id {rec['request_id']}   length {rec['length']}")
    print(f"  packet_hash   {rec['packet_hash'].hex()}   (= H(header | H(ciphertext)))")
    print(f"  H(ciphertext) {op.h1_out.hex()}")
    print(f"  cert ({len(op.cert_out)} bytes): {op.cert_out.hex()}")


def main():
    print("[frontend] chat with Llama — commands: /challenge [id], /cert [id], /list, /quit\n")
    while True:
        try:
            line = input("you> ").strip()
        except EOFError:
            break
        if not line:
            continue
        if line == "/quit":
            break
        if line == "/list":
            for rid, y in turns.items():
                print(f"  #{rid}  response bucket {y}")
            continue
        if line.startswith("/cert"):
            parts = line.split()
            rid = int(parts[1]) if len(parts) > 1 else state["rid"]
            if rid not in turns:
                print("  no such turn (use /list)")
            else:
                show_cert(rid)
            continue
        if line.startswith("/challenge"):
            parts = line.split()
            rid = int(parts[1]) if len(parts) > 1 else state["rid"]
            if rid not in turns:
                print("  no such turn (use /list)")
                continue
            r = challenge(rid)
            if r.get("verified"):
                print(f"  packet #{r.get('rid', rid)} VERIFIED — it is the committed "
                      f"response to request #{r.get('rid', rid)}")
            else:
                print(f"  REJECTED: {r.get('reason', r.get('error'))}")
            continue
        text, rid = say(line)
        print(f"llama> {text}\n       [packet #{rid}]\n")


if __name__ == "__main__":
    main()
