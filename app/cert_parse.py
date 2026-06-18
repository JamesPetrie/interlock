"""Parse and verify interlock certificates from a pcap capture.

Cert frame on the wire (port-0 egress):
  eth:  DST 02:..:01 | SRC 02:..:02 | LEN 0x0094 (148) | DATA[148]
  DATA: reserved[16]=0 || version[4] || interlock_id[4] || bucket_start[8]
        || num_buckets[4] || overall_req[32] || overall_rsp[32] || nonce[16] || tau[32]
  all big-endian.  request cert => overall_rsp==0 ; response cert => overall_req==0.

Verification uses the TEST build's known parameters:
  key   = 0x000..0002   (cert_build .key(2))
  m     = DATA[16:116]   (version .. nonce)
  tau  ?= HMAC-SHA256(key, m)
  overall(packet) = SHA256( be16(datalen) || SHA256( header[16] || SHA256(payload) ) )
  where the test packet is header[16] || payload, datalen = 16+len(payload).

usage: cert_parse.py <pcap> [datalen]      (datalen of the test data packets, default 32)
"""
import sys
import struct
import hashlib
import hmac

KEY = (2).to_bytes(32, "big")
SRC_CERT = bytes.fromhex("020000000002")


def read_pcap(path):
    with open(path, "rb") as f:
        data = f.read()
    magic = data[:4]
    if magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1"):
        end = "<"
    elif magic in (b"\xa1\xb2\xc3\xd4", b"\xa1\xb2\x3c\x4d"):
        end = ">"
    else:
        raise SystemExit("not a pcap (magic %s)" % magic.hex())
    off, frames = 24, []
    while off + 16 <= len(data):
        _, _, caplen, _ = struct.unpack(end + "IIII", data[off:off + 16])
        off += 16
        frames.append(data[off:off + caplen])
        off += caplen
    return frames


def parse_cert(fr):
    if len(fr) < 14 + 148 or fr[6:12] != SRC_CERT:
        return None
    if int.from_bytes(fr[12:14], "big") != 148:
        return None
    d = fr[14:14 + 148]
    return {
        "reserved": d[0:16], "version": int.from_bytes(d[16:20], "big"),
        "interlock_id": int.from_bytes(d[20:24], "big"),
        "bucket_start": int.from_bytes(d[24:32], "big"),
        "num_buckets": int.from_bytes(d[32:36], "big"),
        "overall_req": d[36:68], "overall_rsp": d[68:100],
        "nonce": d[100:116], "tau": d[116:148], "m": d[16:116],
    }


def test_packet(i, datalen):
    """Must match cert_send_spaced.py exactly."""
    plen = datalen - 16
    header = b"HDR\x00" + i.to_bytes(4, "big") + b"\x00" * 8
    payload = bytes(((i + j) & 0xFF) for j in range(plen))
    return header, payload


def overall_of(header, payload, datalen):
    hp = hashlib.sha256(payload).digest()
    rec = hashlib.sha256(header + hp).digest()
    return hashlib.sha256(struct.pack(">H", datalen) + rec).digest()


def main():
    pcap = sys.argv[1]
    datalen = int(sys.argv[2]) if len(sys.argv) > 2 else 32
    frames = read_pcap(pcap)
    certs = [c for c in (parse_cert(f) for f in frames) if c]
    print("frames captured: %d   certificates: %d" % (len(frames), len(certs)))

    # precompute the overall hash for a generous range of test-packet indices
    known = {overall_of(*test_packet(i, datalen), datalen): i for i in range(0, 256)}
    zero = b"\x00" * 32

    tau_ok = ovr_ok = 0
    for n, c in enumerate(certs):
        kind = "REQUEST " if c["overall_rsp"] == zero else \
               "RESPONSE" if c["overall_req"] == zero else "BOTH?"
        tau_calc = hmac.new(KEY, c["m"], hashlib.sha256).digest()
        tgood = tau_calc == c["tau"]
        ov = c["overall_req"] if kind.startswith("REQUEST") else c["overall_rsp"]
        idx = known.get(ov)
        if tgood:
            tau_ok += 1
        if idx is not None:
            ovr_ok += 1
        if n < 8:
            print("\ncert #%d  %s  version=0x%x id=0x%x bucket_start=%d num_buckets=%d"
                  % (n, kind, c["version"], c["interlock_id"], c["bucket_start"], c["num_buckets"]))
            print("  reserved(hdr)= %s  %s" % (c["reserved"].hex(),
                  "[16 zero bytes OK]" if c["reserved"] == b"\x00" * 16 else "[NONZERO!]"))
            print("  overall_req  = %s" % c["overall_req"].hex())
            print("  overall_rsp  = %s" % c["overall_rsp"].hex())
            print("  nonce        = %s" % c["nonce"].hex())
            print("  tau          = %s" % c["tau"].hex())
            print("  tau check    = %s   (HMAC-SHA256(key=0x..02, m))" % ("PASS" if tgood else "FAIL"))
            print("  overall match= %s" % ("test packet #%d" % idx if idx is not None else "no match"))

    print("\n=== SUMMARY: %d certs | tau valid: %d/%d | overall bound to a test packet: %d/%d ==="
          % (len(certs), tau_ok, len(certs), ovr_ok, len(certs)))


if __name__ == "__main__":
    main()
