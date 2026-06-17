"""External verifier — checks that a challenged response is the committed one.
   [diagram: Verifier]

Given an opening, it confirms the certificate is genuine, the response packet is
the one committed in its bucket, and it binds to a real, single-use request. That
establishes complete attribution + no-rewriting for the sampled packet. Turning a
response into an unexplained-information bound U (recomputation or a ZKP) is a
later step and is not done here.
"""
from wire import *                       # noqa: F401,F403 - the wire vocabulary
from common import serve, MAC, IID, N, PORT_VERIFIER, json_to_opening


class Verifier:
    def __init__(self, mac_key, interlock_id, buckets_per_cert=1000, capacity=100_000):
        self.mac_key, self.interlock_id = mac_key, interlock_id
        self.n, self.capacity = buckets_per_cert, capacity
        self.anchors = []
        self.used_ids = set()

    def check_certificate(self, cert):
        """(a) authentic: valid tag, this protocol, our device."""
        c = parse_certificate(cert)
        assert mac_ok(self.mac_key, c["m"], c["tag"])
        assert c["version"] == VERSION and c["interlock_id"] == self.interlock_id
        assert c["num_buckets"] == self.n
        return c

    def anchor(self, nonce, cert):
        """Check the certificate echoes our nonce; returns current bucket B."""
        c = self.check_certificate(cert)
        assert c["nonce"] == nonce
        assert not self.anchors or c["bucket_start"] > self.anchors[-1]
        self.anchors.append(c["bucket_start"])
        return c["bucket_start"] + self.n

    def select(self, rng, current_bucket, window):
        """Uniform over (bucket, byte): size-weighted sampling of transmitted bytes."""
        return (rng.randrange(max(0, current_bucket - window), current_bucket),
                rng.randrange(self.capacity))

    def _check_side(self, cert, overall_field, bucket, hashes, records):
        c = self.check_certificate(cert)                       # (a)
        start = c["bucket_start"]
        assert start <= bucket < start + self.n
        assert overall_hash(hashes) == c[overall_field]        # (b)
        assert bucket_hash(records) == hashes[bucket - start]  # (c)

    @staticmethod
    def _check_packet(direction, records, idx, header, h1):
        """The revealed header + ciphertext hash must reproduce the record."""
        rec, hdr = unpack(RECORD, records[idx]), unpack(HEADER[direction], header)
        assert (hdr["length"], hdr["request_id"]) == (rec["length"], rec["request_id"])
        assert H(header + h1) == rec["packet_hash"]
        return rec["request_id"]

    def verify_opening(self, y, x, op):
        """Checks (a)-(e): genuine certificate, the response is committed in its
        bucket, and it binds to a real single-use request. Returns None if byte x
        is empty, else {rid, h1_in, h1_out, h2} (h1/h2 are committed values a
        later recomputation/ZKP step would consume)."""
        assert op.y == y
        self._check_side(op.cert_out, "overall_out", y, op.hashes_out, op.records_out)
        hit = locate("out", op.records_out, x)                 # (d) byte x -> packet
        if hit is None:
            return None  # past the bucket's content: empty, done
        rid = self._check_packet("out", op.records_out, hit, op.header_out, op.h1_out)
        # (e) input binding: committed inbound, earlier bucket, ID match, single use
        self._check_side(op.cert_in, "overall_in", op.w, op.hashes_in, op.records_in)
        assert op.w <= y
        idx = next(i for i, r in enumerate(op.records_in)
                   if unpack(RECORD, r)["request_id"] == rid)
        assert self._check_packet("in", op.records_in, idx, op.header_in, op.h1_in) == rid
        assert rid not in self.used_ids
        self.used_ids.add(rid)
        return {"rid": rid, "h1_in": op.h1_in, "h1_out": op.h1_out,
                "h2": unpack(HEADER["in"], op.header_in)["recomp_commitment"]}


if __name__ == "__main__":
    def handler(req):
        if req["op"] != "challenge":
            return {"error": "unknown op"}
        op = json_to_opening(req["opening"])
        try:
            binding = Verifier(MAC, IID, buckets_per_cert=N).verify_opening(op.y, 0, op)
        except Exception as e:                                  # noqa: BLE001
            return {"verified": False, "reason": f"{type(e).__name__}: {e}"}
        if binding is None:
            return {"verified": True, "empty": True}
        return {"verified": True, "rid": binding["rid"]}

    serve(PORT_VERIFIER, handler, "verifier")
