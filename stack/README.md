# ilstack

The inference-serving stack that speaks the interlock verification protocol
of `docs/verification-protocol.md` (v6). What runs where, and why, is in
`docs/vcf-stack-plan.md`.

## Layout

```
ilstack/packet.py      the wire format: header, IDs, control packets, certificate, digests
ilstack/crypto.py      pair keys, KEY_COMMIT, AES-CTR over 4-byte token units
ilstack/messages.py    what payloads carry (token ids and parameters), in whole units
ilstack/wire.py        memory and TCP wires
ilstack/clock.py       the bucket clock a sender stamps from, driven by the sync packets
ilstack/log.py         the prover log, in memory or on disk
ilstack/interlock.py   the software interlock
ilstack/dispatcher.py  IDs, timing, sending and receiving packets, loss from the syncs
ilstack/assembly.py    reassembling and decrypting messages
ilstack/frontend.py    the frontend core; api.py its HTTP face; tokenizers.py
ilstack/compute.py     the compute core; engines.py (mock, vLLM)
ilstack/verifier.py    openings, the verifier's checks, the prover's certificate audit
ilstack/cli.py         ilstack keygen | interlock | compute | frontend | verify
deploy/                Slurm scripts for the VCF cluster and the ingress tunnel script
tests/                 50 tests; `python -m pytest`
```

## Run locally

Three processes over TCP on one machine, mock engine and tokenizer:

```
pip install -e '.[frontend,dev]'
ilstack keygen keys
ilstack interlock --keys keys --bucket-ms 10 --bkt-num 100 &
ilstack compute   --keys keys --bucket-ms 10 --bkt-num 100 --engine mock &
ilstack frontend  --keys keys --bucket-ms 10 --bkt-num 100 --log-dir log --http 127.0.0.1:8000 &
curl localhost:8000/v1/chat/completions -H "Authorization: Bearer $(cat keys/api.key)" \
  -H 'Content-Type: application/json' -d '{"messages":[{"role":"user","content":"hi"}],"max_tokens":8}'
ilstack verify --frontend http://127.0.0.1:8000 --keys keys -n 8
```

`verify` anchors with a nonce, picks random (bucket, byte) challenges over
the certified window, fetches the openings and checks them. It needs
`mac.key`, which only the interlock and the verifier hold.

## Parameters

`--bucket-ms`, `--bkt-num`, `--num-slot` and `--device` must agree across
the three processes. The spec's bucket is 1 ms. The interlock accepts a packet
only in the bucket its header names, so a sender's timing jitter must stay
well inside the bucket: with Python senders over TCP, 1 ms buckets on a laptop
lose about a quarter of the packets (each costing a retry), 10 ms lose none.
Measure on the target machines before choosing.

## Run on the VCF cluster

One-time, from the login node:

```
mkdir -p ~/ilstack/run ~/ilstack/keys && cd ~/ilstack
# copy this directory to ~/ilstack/stack, then:
python3 -m venv env && . env/bin/activate
pip install -e 'stack[frontend,dev]' && pip install vllm
ilstack keygen keys
cp stack/deploy/*.sbatch .
ssh-keygen -t ed25519 -N '' -f ~/.ssh/iltunnel   # authorize its .pub on the ingress box, see below
```

Then, with the model name as `M`:

```
sbatch il.sbatch
sbatch --export=ALL,TOKENIZER=$M,MODEL_NAME=$M fe.sbatch
sbatch --export=ALL,ENGINE=vllm,MODEL=$M co.sbatch
```

`BUCKET_MS` and `BKT_NUM` in the environment set the parameters for all three
(defaults 10 and 1000). The frontend job also opens the reverse tunnel to the
ingress box (`deploy/tunnel.sh`, reconnecting; `INGRESS=user@host`, `INGRESS=`
for none), after which the API is public at
`https://static.144.148.104.178.clients.your-server.de`, and the challenge
flow runs from anywhere that has `mac.key`:

```
ilstack verify --frontend https://static.144.148.104.178.clients.your-server.de --keys keys -n 8
```

Frontend and compute may be restarted independently; the interlock keeps
running and re-accepts them. Logs are in `~/ilstack/run/`. Tell the cluster
admins before reserving the GPU nodes, and keep `--time` inside your working
window.

## Ingress box (Hetzner)

Set up once, as root. Caddy comes from its own apt repository
(`dl.cloudsmith.io/public/caddy/stable`) and obtains the Let's Encrypt
certificate for the box's Hetzner hostname by itself; `/etc/caddy/Caddyfile`
is three lines:

```
static.144.148.104.178.clients.your-server.de {
	reverse_proxy 127.0.0.1:8000
}
```

The tunnel logs in as `iltunnel` (`useradd -r -m -s /usr/sbin/nologin`), whose
`~/.ssh/authorized_keys` holds the frontend's key restricted to the one
forward:

```
restrict,port-forwarding,permitlisten="127.0.0.1:8000" ssh-ed25519 AAAA... iltunnel@vcf-frontend
```

`ufw allow 80/tcp` and `443/tcp` complete it. The frontend's HTTP port is then
reachable only through the proxy; nothing else on the box is exposed.
