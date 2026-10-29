#!/bin/bash
# End to end through the FPGA on as10: compute on the server port, frontend on the client port, both releasing buckets
# through the kernel over raw frames (an etf queue releases the frames at their departure times, a wire time apart for the 25 G links); N requests through the HTTP API, sequential then
# concurrent; health counters; the verifier client (anchor, openings). Run as root from ~/ilstack:
#   sudo -n nohup bash run/e2e.sh <requests> > run/e2e.out 2>&1 &
cd /home/amodo/ilstack || exit 1
rm -f run/e2e.done
N=${1:-200}; CHALLENGES=${2:-8}; PROMPT_KB=${3:-0}; CONC=${4:-32}; FE_IF=enp1s0np0; CO_IF=enp66s0np0; K=run/e2e-keys
# PROMPT_KB > 0: each request carries a prompt of that size (many packets each way; the mock engine echoes it back),
# so that the verifier's challenges can land inside content and not only prove buckets empty
PROMPT=""; [ "$PROMPT_KB" -gt 0 ] && PROMPT=$(env/bin/python -c "import sys; print(('lorem ipsum dolor sit amet ' * (($PROMPT_KB * 1024) // 27 + 1))[:$PROMPT_KB * 1024])")
MAXTOK=64; [ "$PROMPT_KB" -gt 0 ] && MAXTOK=$((PROMPT_KB * 400))
env/bin/python -m ilstack.cli keygen $K > /dev/null
printf '%062d63\n' 0 > $K/mac.key                               # the FPGA's certificate key: 31 zero bytes then 99 (fabric_bridge .key(99))
for IF in $FE_IF $CO_IF; do
  tc qdisc replace dev $IF root etf clockid CLOCK_TAI delta 300000
done
exec 3<>/dev/cpu_dma_latency; printf '\x00\x00\x00\x00' >&3
COMMON="--keys $K --release kernel --rate-limit --core none --ahead 10 --bucket-ms 1 --bkt-num 1000 --num-slot 64 --device 66 --delta-unit 1e-8 --fill 0.95 --target 0.05 --slots 64 --link-mbps 1000"
rm -rf run/e2e-log
env/bin/python -m ilstack.cli compute $COMMON --interlock raw:$CO_IF --engine mock > run/e2e-co.log 2>&1 &
CO=$!
env/bin/python -m ilstack.cli frontend $COMMON --interlock raw:$FE_IF --http 127.0.0.1:8000 --log-dir run/e2e-log --timeout 10 --retries 2 > run/e2e-fe.log 2>&1 &
FE=$!
KEY=$(cat $K/api.key)
for i in $(seq 60); do curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/health 2>/dev/null | grep -q 200 && break; sleep 1; done
sleep 3
echo "--- health at start"; curl -s http://127.0.0.1:8000/health | head -c 700; echo
T0=$(date +%s.%N); OK=0; FAIL=0
for i in $(seq $N); do
  code=$(curl -s -o run/e2e-resp.json -w "%{http_code}" -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
    -d "{\"model\":\"mock\",\"messages\":[{\"role\":\"user\",\"content\":\"request $i: tell me about bucket $i in some detail please $PROMPT\"}],\"max_tokens\":$MAXTOK}" \
    http://127.0.0.1:8000/v1/chat/completions)
  if [ "$code" = 200 ]; then OK=$((OK + 1)); else FAIL=$((FAIL + 1)); [ $FAIL -le 3 ] && { echo "request $i: HTTP $code"; head -c 300 run/e2e-resp.json; echo; }; fi
done
T1=$(date +%s.%N)
echo "sequential: $OK ok, $FAIL failed in $(echo "$T1 - $T0" | bc) s; a response of $(wc -c < run/e2e-resp.json) bytes:"; head -c 300 run/e2e-resp.json; echo
echo "--- health after the sequential requests"; curl -s http://127.0.0.1:8000/health | python3 -c "import json,sys; h=json.loads(sys.stdin.read()); print(h['frontend']); print(h['dispatcher']); c=h['clock']; print({k: c[k] for k in ('lost','locked','landed_min','landed_mean','landed_max','lock_spread_us')})"
T0=$(date +%s.%N)
for i in $(seq $CONC); do
  curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
    -d "{\"model\":\"mock\",\"messages\":[{\"role\":\"user\",\"content\":\"concurrent $i\"}],\"max_tokens\":64}" http://127.0.0.1:8000/v1/chat/completions &
done | sort | uniq -c
T1=$(date +%s.%N); echo "$CONC concurrent in $(echo "$T1 - $T0" | bc) s"
sleep 3
echo "--- health at end"; curl -s http://127.0.0.1:8000/health; echo
echo "--- verifier"; env/bin/python -m ilstack.cli verify --frontend http://127.0.0.1:8000 --api-key $KEY --keys $K -n $CHALLENGES --anchors 1 > run/e2e-verify.txt 2>&1
grep -v "^challenge" run/e2e-verify.txt; echo "challenges: $(grep -c "^challenge" run/e2e-verify.txt); empty verified: $(grep -c "empty: VERIFIED" run/e2e-verify.txt); with content verified: $(grep "^challenge" run/e2e-verify.txt | grep -v empty | grep -c VERIFIED); failed: $(grep "^challenge" run/e2e-verify.txt | grep -vc VERIFIED)"; grep "^challenge" run/e2e-verify.txt | grep -v empty | head -4
kill $FE $CO 2>/dev/null; sleep 2; kill -9 $FE $CO 2>/dev/null
exec 3>&-
for IF in $FE_IF $CO_IF; do tc qdisc del dev $IF root 2>/dev/null; done
chown -R amodo:amodo /home/amodo/ilstack/run
touch run/e2e.done
