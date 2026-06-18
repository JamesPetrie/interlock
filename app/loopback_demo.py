#!/usr/bin/env python3
"""Non-interactive loopback driver for the interlock demo (validation harness).

Sends one tokenized prompt through the interlock on the PORT 0 NIC, captures the
certified response, runs the in-band ZK challenge, and prints the combined certificate +
ZK-proof panel. This is the scripted equivalent of `infcli.py chat` + `/prove`, used to
validate the whole chain on the Spark (both interlock ports wired to the same box).

  python3 loopback_demo.py --iface enxb8fbb3b1f53c --model /models/llama-2-7b-hf
"""
import argparse
import sys

sys.path.insert(0, "/app")
import infcli


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iface", required=True)
    ap.add_argument("--model", default="/models/llama-2-7b-hf")
    ap.add_argument("--prompt", default="Question: What is the capital of France?\nAnswer:")
    ap.add_argument("--gap-ms", type=int, default=300)
    ap.add_argument("--wait-ms", type=int, default=15000)
    ap.add_argument("--challenge-timeout", type=int, default=1800)
    ap.add_argument("--key", default=infcli.DEFAULT_KEY.hex(), type=lambda s: infcli.ub(s))
    a = ap.parse_args()

    t = infcli.tok(a)
    ids = t(a.prompt, add_special_tokens=True)["input_ids"]
    print("→ sending %d-token prompt: %r" % (len(ids), a.prompt), flush=True)
    entry = infcli.send_payload(a, infcli.pack_ids(ids))
    print("rid=%d  response captured: %s" % (entry["rid"], bool(entry["response_data"])),
          flush=True)
    infcli._report(entry, a.key)
    if not entry["response_data"]:
        print("NO RESPONSE — is the interlock forwarding PORT0<->PORT1?")
        return 2
    rsp = infcli.unpack_ids(infcli.ub(entry["response_data"])[infcli.HDR:])
    print("← response (%d tokens): %r" % (len(rsp), t.decode(rsp, skip_special_tokens=True)),
          flush=True)
    print("\n→ triggering in-band ZK challenge (proof runs on the Spark)...", flush=True)
    result = infcli.do_challenge(a, entry["rid"], lambda s: print(s, flush=True))
    if result is None:
        print("challenge TIMED OUT")
        return 3
    infcli.combined_panel(a, entry["rid"], result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
