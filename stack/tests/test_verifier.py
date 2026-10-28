import asyncio
import dataclasses
import time

from harness import MAC, Stack, run

from ilstack import packet as P
from ilstack.verifier import Opening, Verifier, build_opening

PARAMS = P.Params(num_slot=8, bkt_num=10, bucket_ms=10)


async def certified_stack(s: Stack) -> Verifier:
    """Some traffic, then an anchor so that everything so far is certified."""
    await s.fe.complete(list(range(1, 2001)), {"max_tokens": 2000})           # several packets each way
    await asyncio.gather(*(s.fe.complete([i] * 3, {"max_tokens": 3}) for i in range(1, 5)))
    v = Verifier(MAC, s.params)
    nonce = v.new_nonce()
    t0 = time.time()
    cert = await s.fe.nonce(nonce, timeout=2)
    res = v.anchor(nonce, cert, t0, time.time())
    assert res["ok"], res
    return v


def all_packets(log, direction):
    out = []
    for cert in log.certificates:
        for b in range(cert.bkt_start, cert.bkt_start + cert.bkt_num):
            out += [P.parse(raw) for raw in log.bucket(direction, b)]
    return out


def test_every_logged_byte_opens_and_verifies():
    async def main():
        async with Stack(PARAMS) as s:
            v = await certified_stack(s)
            for direction in P.DIRECTIONS:
                pkts = all_packets(s.fe_log, direction)
                assert len(pkts) >= 5
                seen_ref = False
                for pkt in pkts:
                    start = sum(len(p.payload) for p in s.fe_log.bucket(direction, pkt.id.bucket) and
                                [P.parse(r) for r in s.fe_log.bucket(direction, pkt.id.bucket)] if p.id.slot < pkt.id.slot)
                    for byte in (start, start + len(pkt.payload) - 1):
                        op = build_opening(s.params, s.fe_log, direction, pkt.id.bucket, byte)
                        res = v.check(Opening.from_json(op.to_json()))
                        assert res["verified"], (pkt.id, byte, res)
                        assert res["id"] == pkt.id and res["reference"] == pkt.reference
                        seen_ref |= op.reference is not None
                assert seen_ref                                              # continuations and responses open their reference
            # responses reference requests across directions; the reference opening carries the KEY_COMMIT
            resp = all_packets(s.fe_log, P.OUTBOUND)[0]
            op = build_opening(s.params, s.fe_log, P.OUTBOUND, resp.id.bucket, 0)
            assert op.reference.dir == P.INBOUND and v.check(op)["checks"]["ref_key_commit"]
    run(main())


def test_empty_proofs_and_random_challenges():
    async def main():
        async with Stack(PARAMS) as s:
            v = await certified_stack(s)
            latest = v.anchors[-1].certificate
            empty_bucket = next(b for b in range(latest.bkt_start, latest.bkt_start + latest.bkt_num)
                                if not s.fe_log.bucket(P.OUTBOUND, b))
            op = build_opening(s.params, s.fe_log, P.OUTBOUND, empty_bucket, 5)
            res = v.check(op)
            assert res["verified"] and res["checks"]["empty"] and op.hit.index is None and op.reference is None
            full = all_packets(s.fe_log, P.OUTBOUND)[0]
            beyond = build_opening(s.params, s.fe_log, P.OUTBOUND, full.id.bucket, s.params.capacity - 1)
            assert v.check(beyond)["checks"]["empty"]
            assert build_opening(s.params, s.fe_log, P.OUTBOUND, 10 ** 6, 0) is None     # not certified
            for _ in range(20):
                d, bucket, byte = v.select(window=10 ** 6)
                assert d == P.OUTBOUND and 0 <= bucket < latest.bkt_start + latest.bkt_num and byte < s.params.capacity
                res = v.check(build_opening(s.params, s.fe_log, d, bucket, byte))
                assert res["verified"], res
    run(main())


def test_tampering_is_caught():
    async def main():
        async with Stack(PARAMS) as s:
            v = await certified_stack(s)
            resp = all_packets(s.fe_log, P.OUTBOUND)[0]
            good = build_opening(s.params, s.fe_log, P.OUTBOUND, resp.id.bucket, 0)
            assert v.check(good)["verified"]

            def failing(op):
                res = v.check(op)
                assert not res["verified"]
                return [k for k, ok in res["checks"].items() if not ok]

            h = good.hit
            bad_cert = dataclasses.replace(h.certificate, auth_tag=bytes(32))
            assert "certificate" in failing(dataclasses.replace(good, hit=dataclasses.replace(h, certificate=bad_cert)))
            digests = list(h.bkt_digests); digests[0] = bytes(32)
            assert "cert_digest" in failing(dataclasses.replace(good, hit=dataclasses.replace(h, bkt_digests=digests)))
            lens = list(h.pld_lens); lens[h.index] += 4
            assert "bkt_digest" in failing(dataclasses.replace(good, hit=dataclasses.replace(h, pld_lens=lens)))
            assert "pkt_digest" in failing(dataclasses.replace(good, hit=dataclasses.replace(h, pld_digest=bytes(32))))
            other = h.header[:3] + bytes([h.header[3] ^ 1]) + h.header[4:]      # a different PLD_LEN in the header
            assert "pkt_digest" in failing(dataclasses.replace(good, hit=dataclasses.replace(h, header=other)))
            assert "byte_in_packet" in failing(dataclasses.replace(good, byte=s.params.capacity - 1))
            assert "reference" in failing(dataclasses.replace(good, reference=None))     # a response must open its request
            r = good.reference
            assert "ref_pkt_digest" in failing(dataclasses.replace(good, reference=dataclasses.replace(r, pld_digest=bytes(32))))
            wrong = dataclasses.replace(r, index=r.index + 1)                        # not the record the ID names
            assert failing(dataclasses.replace(good, reference=wrong))
            # a certificate from another device, or with a bad nonce, is not an anchor
            assert not v.anchor(b"x" * 16, v.anchors[-1].certificate, 0, 1)["ok"]
            assert not Verifier(MAC, P.Params(device=9)).anchor(v.anchors[-1].nonce, v.anchors[-1].certificate, 0, 1)["ok"]
    run(main())
