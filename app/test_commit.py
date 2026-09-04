#!/usr/bin/env python3
"""Unit tests for commit.audit_search — the lost-acknowledgement resolver.

No hardware needed: a certificate is just a dict with a bucket range and the two
direction roots, so a period's traffic can be synthesised and the roots computed
with the same functions the audit uses. What is being checked is that the search
recovers exactly the frames the device committed and nothing else — a search that
"passed" by finding some other combination would be worse than no search at all.
"""
import struct
import sys

import commit

LO, N = 1_000_000, 1000


def data(bucket, ident, payload=b""):
    """A canonical DATA blob declaring `bucket`, shaped like canon_tx builds it."""
    return struct.pack("!IIQ", len(payload), bucket, ident) + b"\x00" * 48 + payload


def cert_over(by_bucket, direction="req"):
    root = commit.epoch_root(by_bucket, LO, N)
    return {"bucket_start": LO, "num_buckets": N,
            "overall_req": root if direction == "req" else b"\x00" * 32,
            "overall_rsp": root if direction == "rsp" else b"\x00" * 32}


def check(name, got, want):
    print("  %-46s %s" % (name, "ok" if got == want else "FAIL (%r != %r)" % (got, want)))
    return got == want


def main():
    ok = True

    # Everything acknowledged: the plain audit already passes, and the search must
    # not go looking for an explanation it does not need.
    truth = {LO + 5: [data(LO + 5, 2)], LO + 900: [data(LO + 900, 4)]}
    c = cert_over(truth)
    ok &= check("all acked -> pass, nothing resolved",
                commit.audit_search(truth, {}, c, "req"), (True, []))

    # One frame committed but never acknowledged. The plain audit fails; the search
    # must find it, and name the bucket it came from.
    lost = {LO + 900: [data(LO + 900, 4)]}
    partial = {LO + 5: [data(LO + 5, 2)]}
    ok &= check("plain audit misses a lost ack",
                commit.audit(partial, c, "req"), False)
    ok &= check("search recovers the one lost ack",
                commit.audit_search(partial, lost, c, "req"), (True, [LO + 900]))

    # Three lost at once, mixed in with frames the device really did reject. The
    # rejected ones must stay out: including them would change the root.
    truth3 = {LO + i: [data(LO + i, i)] for i in (10, 20, 30, 40)}
    c3 = cert_over(truth3)
    confirmed3 = {LO + 10: [data(LO + 10, 10)]}
    ambiguous3 = {LO + i: [data(LO + i, i)] for i in (20, 30, 40)}
    ambiguous3[LO + 55] = [data(LO + 55, 55)]        # genuinely rejected, not committed
    ok &= check("search recovers 3 of 4, leaves the rejected one out",
                commit.audit_search(confirmed3, ambiguous3, c3, "req"),
                (True, [LO + 20, LO + 30, LO + 40]))

    # Tampering must still fail. If a retained frame's bytes differ from what crossed,
    # no subset of the ambiguous set can rescue it.
    tampered = {LO + 5: [data(LO + 5, 2, b"x")], LO + 900: [data(LO + 900, 4)]}
    ok &= check("altered payload still fails",
                commit.audit_search(tampered, {}, c, "req"), (False, None))

    # A frame the device did NOT commit must not be smuggled in by the search.
    extra = {LO + 5: [data(LO + 5, 2)], LO + 900: [data(LO + 900, 4)],
             LO + 700: [data(LO + 700, 9)]}
    ok &= check("extra committed-looking frame still fails",
                commit.audit_search(extra, {}, c, "req"), (False, None))

    # Buckets outside the certified period are irrelevant and must not be considered.
    ok &= check("out-of-period ambiguity ignored",
                commit.audit_search(truth, {LO + N + 3: [data(LO + N + 3, 7)]}, c, "req"),
                (True, []))

    # Direction selects which root is checked; an rsp cert must not pass a req audit.
    crsp = cert_over(truth, "rsp")
    ok &= check("direction is honoured",
                commit.audit_search(truth, {}, crsp, "rsp"), (True, []))
    ok &= check("wrong direction fails",
                commit.audit_search(truth, {}, crsp, "req"), (False, None))

    # Too many unknowns: refuse rather than burn minutes on 2^k hypotheses.
    many = {LO + 100 + i: [data(LO + 100 + i, i)]
            for i in range(commit.MAX_AMBIGUOUS + 1)}
    ok &= check("gives up above MAX_AMBIGUOUS",
                commit.audit_search({}, many, cert_over(many), "req"), (False, None))

    print("\ncommit.audit_search: %s" % ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
