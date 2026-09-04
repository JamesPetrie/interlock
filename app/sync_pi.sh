#!/usr/bin/env bash
# Push the client half of the app to the Raspberry Pi.
#
#   ./sync_pi.sh                 # to the default host
#   PI=other-host ./sync_pi.sh   # somewhere else
#   ./sync_pi.sh --check         # report drift, change nothing
#
# WHY THIS EXISTS. The Pi runs the client: it tokenizes, seals the request under
# AES-128-GCM, and is the only side that can open the response. Those files used
# to get there by hand-typed scp, which meant the demo depended on a second
# machine whose contents were outside version control -- nothing recorded which
# revision the Pi was on, and drift stayed invisible until a GCM tag failed and
# looked like a wire fault. This makes the Pi a deployment target of the repo.
#
# The Pi deliberately gets a COPY of the two reference modules from VerInf rather
# than the whole repo: it has no GPU, no torch, and no need for the prover. But a
# copy is a drift risk, which is exactly why --check exists -- run it before a
# demo and the answer is a checksum, not a hope.
set -uo pipefail

PI="${PI:-2a-rpi}"
APP="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERINF="${VERINF:-$(cd "$APP/../.." && pwd)/VerInf}"
DEST="${DEST:-fpe}"

# The client half, and only it. infcli drives the wire; canon_tx is the
# bucket-timed port; commit recomputes the certificate hierarchy; ilk_crypto
# seals and opens; tok owns the text layer.
FILES=(infcli.py canon_tx.py commit.py ilk_crypto.py tok.py pi_agent.py)
# The cipher the circuit is composed against -- one implementation, or the wire
# and the proof drift apart.
REFS=(token_recorder.py poseidon_gl.py)

check=0; [ "${1:-}" = "--check" ] && check=1
fail=0

sum_local() { sha256sum "$1" | cut -c1-16; }
sum_remote() { ssh "$PI" "sha256sum $1 2>/dev/null" | cut -c1-16; }

printf '%-22s %-18s %-18s %s\n' FILE LOCAL PI STATUS
for f in "${FILES[@]}"; do
    l=$(sum_local "$APP/$f"); r=$(sum_remote "$DEST/$f")
    if [ "$l" = "$r" ]; then st="ok"
    elif [ $check -eq 1 ]; then st="DRIFT"; fail=1
    else scp -q "$APP/$f" "$PI:$DEST/$f" && st="pushed"; fi
    printf '%-22s %-18s %-18s %s\n' "$f" "$l" "${r:-absent}" "$st"
done
for f in "${REFS[@]}"; do
    l=$(sum_local "$VERINF/prover/ref/$f"); r=$(sum_remote "$DEST/ref/$f")
    if [ "$l" = "$r" ]; then st="ok"
    elif [ $check -eq 1 ]; then st="DRIFT"; fail=1
    else ssh "$PI" "mkdir -p $DEST/ref" && scp -q "$VERINF/prover/ref/$f" "$PI:$DEST/ref/$f" && st="pushed"; fi
    printf '%-22s %-18s %-18s %s\n' "ref/$f" "$l" "${r:-absent}" "$st"
done

# The pre-shared key is NOT synced. It is a secret, it is provisioned once out of
# band, and infcli runs under sudo on the Pi so root must be able to read it.
echo
if ssh "$PI" "sudo test -r /root/.interlock/psk" 2>/dev/null; then
    echo "psk: present for root on $PI"
else
    echo "psk: MISSING for root on $PI -- provision it (not synced; it is a secret):"
    echo "     python3 ilk_crypto.py --provision"
    echo "     scp ~/.interlock/psk $PI:~/.interlock/psk"
    echo "     ssh $PI 'sudo mkdir -p /root/.interlock && sudo cp ~/.interlock/psk /root/.interlock/psk'"
    fail=1
fi

[ $check -eq 1 ] && [ $fail -eq 0 ] && echo "in sync"
exit $fail
