#!/usr/bin/env python3
"""Frame classification tests for infcli.

Getting these wrong is quiet and expensive. Both discriminators here were
originally written against frame LENGTH, and length is not a discriminator on
this wire: a 36-token response is exactly as long as a certificate, and a
forwarded keep-alive probe is exactly as long as a sync beacon. Either confusion
corrupts the response-direction byte-audit rather than raising anything.
"""
import struct
import sys

import infcli as I

SERVER = I.SERVER
CLIENT = I.CLIENT


def eth(dst, src, data):
    f = dst + src + len(data).to_bytes(2, "big") + data
    return f + b"\x00" * (60 - len(f)) if len(f) < 60 else f


def canon(bucket, ident, payload=b""):
    return struct.pack("!IIQ", len(payload), bucket, ident) + b"\x00" * 48 + payload


def forwarded(payload=b"", bucket=5000, ident=4):
    return eth(CLIENT, SERVER, canon(bucket, ident, payload))


def sync(bucket=5000, first_arr=1234):
    # Sync's canonical header is (FIRST_ARR, BUCKET, SYNC_ID) — id 1, not a length.
    return eth(CLIENT, SERVER,
               struct.pack("!IIQ", first_arr, bucket, I.SYNC_ID) + b"\x00" * 48)


def cert(bucket_start=5000):
    m = (b"\x00\x00\x00\x01" + b"\x00\x00\x00\x02"
         + struct.pack("!I", bucket_start) + struct.pack("!I", 1000)
         + b"\xaa" * 16 + b"\xbb" * 32 + b"\xcc" * 32 + b"\xdd" * 32)
    return eth(CLIENT, SERVER, b"\x00" * I.CANON_HDR + m + b"\xee" * 32)


def check(name, got, want):
    ok = got == want
    print("  %-52s %s" % (name, "ok" if ok else "FAIL (%r != %r)" % (got, want)))
    return ok


def main():
    ok = True
    app = b"REQ\x00" + (7).to_bytes(4, "big") + b"\x00" * 8

    # The ordinary cases.
    rsp = forwarded(app + b"\x01" * 96)
    ok &= check("inference response is forwarded", I.is_forwarded(rsp), True)
    ok &= check("inference response is a data frame", I.is_data_frame(rsp), True)
    ok &= check("inference response is not a cert", I.is_cert_frame(rsp), False)

    c = cert()
    ok &= check("certificate parses", I.parse_cert(c) is not None, True)
    ok &= check("certificate is not forwarded traffic", I.is_forwarded(c), False)
    ok &= check("certificate bucket_start read back",
                I.parse_cert(c)["bucket_start"], 5000)

    s = sync()
    ok &= check("sync beacon is not forwarded traffic", I.is_forwarded(s), False)
    ok &= check("sync beacon is recognised", I.is_sync_frame(s), True)

    # The two collisions that length-based tests get wrong.
    #
    # A 36-token response is 64 + 16 + 144 = 224 canonical bytes — the exact size
    # of a certificate. It must still count as forwarded traffic, or the audit
    # silently drops a real packet from the period it is recomputing.
    collide = forwarded(app + b"\x02" * (224 - I.CANON_HDR - I.APP_HDR))
    ok &= check("224-byte response is not mistaken for a cert",
                I.is_cert_frame(collide), False)
    ok &= check("224-byte response counts as forwarded",
                I.is_forwarded(collide), True)
    ok &= check("224-byte response does not parse as a cert",
                I.parse_cert(collide), None)

    # A forwarded keep-alive probe is header-only — the same 64 bytes as a sync
    # beacon. It IS committed by OUTWARD, so it has to survive classification.
    ka = forwarded(b"", bucket=5001, ident=6)
    ok &= check("keep-alive probe counts as forwarded", I.is_forwarded(ka), True)
    ok &= check("keep-alive probe is not a sync", I.is_sync_frame(ka), False)
    ok &= check("keep-alive probe carries no app header",
                I.is_data_frame(ka), False)

    # Direction still matters: our own transmissions must not be counted as the
    # interlock's output.
    ours = eth(SERVER, CLIENT, canon(5000, 8, app))
    ok &= check("our own direction is not forwarded", I.is_forwarded(ours), False)

    # And the retained set for the response audit must contain everything above
    # that crossed, keyed by the bucket each frame declares.
    by = I.rsp_by_bucket([rsp, ka, c, s, ours, collide])
    ok &= check("retained set keeps exactly the forwarded frames",
                sorted((k, len(v)) for k, v in by.items()),
                [(5000, 2), (5001, 1)])

    print("\ninfcli frame classification: %s" % ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
