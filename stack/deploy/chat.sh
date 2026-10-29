#!/bin/bash
# Talk to the stack on the cluster through the public ingress: fetch the API key, check the health (OK or ERROR;
# it never starts jobs), then read messages from the terminal and print the replies. Runs on your Mac.
#   bash stack/deploy/chat.sh            # interactive; empty line or Ctrl-D ends it
#   echo "hello" | bash stack/deploy/chat.sh
LOGIN=${LOGIN:-peter_drotos@vcf.lucidcomputing.ai}
URL=${URL:-https://static.144.148.104.178.clients.your-server.de}
MODEL=${MODEL:-mock}

say() { printf '%s\n' "$*" >&2; }
health() { curl -s -m 10 "$URL/health" 2>/dev/null | python3 -c '
import json, sys
try:
    h = json.load(sys.stdin)
except Exception:
    sys.exit(1)
f, c = h["frontend"], h["clock"]
lock = "locked" if c.get("locked") else "not locked"
print("completions %d, retries %d, lost %d, timeouts %d, certificates ok %d mismatched %d, clock %s" % (
    f["completions"], f["retries"], f["lost"], f["timeouts"], f["certificates_ok"], f["certificates_mismatched"], lock))
sys.exit(0 if h.get("status") == "ok" else 1)'; }

say "fetching the API key from the cluster"
KEY=$(ssh -o BatchMode=yes -o ConnectTimeout=20 "$LOGIN" 'cat ilstack/keys/api.key' 2>/dev/null)
[ -n "$KEY" ] || { say "ERROR: could not read the API key over ssh"; exit 1; }

H=$(health)
if [ -n "$H" ]; then say "OK: $H"; else say "ERROR: the stack is not answering at $URL (start it on the cluster: cd ilstack; IL=\$(sbatch --parsable il.sbatch); sbatch --dependency=after:\$IL fe.sbatch; sbatch --gpus=0 --dependency=after:\$IL co.sbatch)"; exit 1; fi

say "type a message and press return (empty line ends):"
while IFS= read -r -p '> ' MSG; do
  [ -z "$MSG" ] && break
  T0=$(python3 -c 'import time; print(time.time())')
  RESP=$(python3 -c 'import json, sys; print(json.dumps({"model": sys.argv[1], "messages": [{"role": "user", "content": sys.argv[2]}], "max_tokens": 256}))' "$MODEL" "$MSG" |
    curl -s -m 60 -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d @- "$URL/v1/chat/completions")
  T1=$(python3 -c 'import time; print(time.time())')
  python3 -c '
import json, sys
t = float(sys.argv[1])
try:
    r = json.loads(sys.stdin.read())
except Exception:
    print("ERROR: no valid reply (%.0f ms)" % (t * 1000)); sys.exit()
if "choices" in r:
    print(r["choices"][0]["message"]["content"])
    print("[%.0f ms, %s tokens]" % (t * 1000, r.get("usage", {}).get("completion_tokens", "?")))
else:
    print("ERROR:", r.get("detail", r))' "$(python3 -c "print($T1 - $T0)")" <<< "$RESP"
done
say "health now: $(health)"
