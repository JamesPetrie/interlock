#!/usr/bin/env python3
"""Multi-turn context validation: drive several chat turns through the interlock,
confirming each turn's request is the WHOLE prior transcript re-tokenized into one
message. Reuses infcli's chat primitives (build_request_text / send_payload / truncate),
so it exercises the same code path as `infcli.py chat`. No proof here — see
loopback_demo.py or the chat `/prove` command.

  python3 multiturn_demo.py --iface enxb8fbb3b1f53c \
      --turns "What is the capital of France?" "And of Germany?" "Name a famous river there."
"""
import argparse
import sys

sys.path.insert(0, "/app")
import infcli


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iface", required=True)
    ap.add_argument("--model", default="/models/llama-2-7b-hf")
    ap.add_argument("--gap-ms", type=int, default=300)
    ap.add_argument("--wait-ms", type=int, default=15000)
    ap.add_argument("--challenge-timeout", type=int, default=1800)
    ap.add_argument("--key", default=infcli.DEFAULT_KEY.hex(), type=lambda s: infcli.ub(s))
    ap.add_argument("--system", default="")
    ap.add_argument("--user-tag", default="Question:")
    ap.add_argument("--bot-tag", default="Answer:")
    ap.add_argument("--turns", nargs="+", required=True)
    a = ap.parse_args()

    t = infcli.tok(a)
    history = []
    for turn in a.turns:
        req_text = infcli.build_request_text(a, history, turn)
        req_ids = t(req_text, add_special_tokens=True)["input_ids"]
        print("\nyou> %s" % turn, flush=True)
        print("    [request = whole transcript so far: %d tokens]" % len(req_ids), flush=True)
        if infcli.HDR + 4 * len(req_ids) > infcli.MAX_PAYLOAD:
            print("    context over wire limit — stopping"); break
        entry = infcli.send_payload(a, infcli.pack_ids(req_ids))
        if not entry["response_data"]:
            print("    NO RESPONSE (rid=%d) — is the interlock forwarding?" % entry["rid"])
            return 2
        rsp = infcli.unpack_ids(infcli.ub(entry["response_data"])[infcli.HDR:])
        ans = t.decode(rsp, skip_special_tokens=True)
        cut = ans.find("\n" + a.user_tag)
        if cut != -1:
            ans = ans[:cut]
        ans = ans.strip()
        history.append((turn, ans))
        rc = "y" if entry["request_cert"] else "-"
        sc = "y" if entry["response_cert"] else "-"
        print("bot> %s\n    [rid=%d  certs req=%s rsp=%s]" % (ans, entry["rid"], rc, sc),
              flush=True)
    print("\n--- final transcript (what the next turn would extend) ---", flush=True)
    print(infcli.build_request_text(a, history, "<your next question>"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
