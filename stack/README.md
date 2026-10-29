# ilstack

The inference-serving stack that speaks the interlock verification protocol
of `docs/verification-protocol.md` (v6). What runs where, and why, is in
`docs/vcf-stack-plan.md`.

## Layout

```
ilstack/packet.py      the wire format: header, IDs, control packets, certificate, digests
ilstack/crypto.py      pair keys, KEY_COMMIT, AES-CTR over 4-byte token units
ilstack/messages.py    what payloads carry (token ids and parameters), in whole units
ilstack/wire.py        memory, TCP and raw wires for the event loop
ilstack/clock.py       the bucket clock a sender stamps from, driven by the sync packets
ilstack/log.py         the prover log, in memory or on disk
ilstack/interlock.py   the software interlock
ilstack/dispatcher.py  IDs, buckets, the release (process, kernel, hardware), loss from the syncs
ilstack/pacer.py       the pacer process (the `process` release) and its framing
ilstack/links.py       the TCP and raw links outside the event loop; a bucket in one call, departure times
ilstack/runtime.py     a process with a deadline: pinning, real-time priority, low jitter, the collector off
ilstack/assembly.py    reassembling and decrypting messages
ilstack/frontend.py    the frontend core; api.py its HTTP face; tokenizers.py
ilstack/compute.py     the compute core; engines.py (mock, vLLM)
ilstack/verifier.py    openings, the verifier's checks, the prover's certificate audit
ilstack/timing.py      the timing tools: stub receiver, sender, raw-frame counter
ilstack/cli.py         ilstack keygen | interlock | compute | frontend | verify | timing-*
deploy/                Slurm scripts for the cluster, the as10 bench scripts, the ingress tunnel, chat.sh
tests/                 61 tests; `python -m pytest`
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
only in the bucket its header names, so the whole path from the sender's
transmit instant to the interlock's check must fit inside the bucket. Who
releases a bucket at its instant is `--release`:

- `process`: a pacer process on a core of its own (`--core`), real-time
  where allowed, locked to the interlock through the sync feedback, spinning
  on the instant; `--rate-limit` makes it send frame by frame at the link's
  wire time (`--link-mbps`) instead of one hand-over. For hosts without a
  time-aware queue or root (the cluster today).
- `kernel`: the dispatcher stamps each bucket with its instant (CLOCK_TAI)
  and hands it to the kernel `ahead` buckets early; an `etf` queue on the
  interface releases it from a kernel timer (in software mode up to its
  `delta` early, so the dispatcher reads the delta and stamps that much
  later). Needs a raw link and, once,
  `tc qdisc replace dev <if> root etf clockid CLOCK_TAI delta 300000` (`fq`
  reorders a bucket's shorter frames; with it use `quantum 100000`).
  `--rate-limit` stamps every frame a wire time apart, for a link faster
  than the device behind it.
- `hardware`: `kernel` with the queue offloaded to a TSN NIC.

`--target` is where in the bucket the first packet lands, `--fill` how far
into the bucket packets may still be sent, `--delta-unit` the interlock's
DELTA unit (1e-8 on the FPGA). The interlock busy-polls on a core of its own
(`--spin --core`). `/health` reports where packets land in their bucket
(`landed_*`, as a fraction) and the lock's spread.

Measured with 1 ms buckets and the mock engine, 270 requests, no drops:

| Path | landing in the bucket | pacer error |
|---|---|---|
| frontend to interlock, same VM | 0.25 to 0.30 | 7 µs mean, 13 µs max |
| compute to interlock, across the cluster network | 0.20 to 0.57 | 10 µs mean, 34 µs max |

On a laptop running all five processes the loopback path alone jitters by
most of a millisecond and a few percent of packets arrive late; use 10 ms
there.

## Timing tools

Three commands measure one direction of the link with nothing else in the
path, the way the device will see it:

```
ilstack timing-stub --listen 0.0.0.0:17001 --bucket-ms 1 --core auto      # receiver: timestamp, judge, sync per bucket
ilstack timing-send --stub HOST:17001 --bucket-ms 1 --slots 32 --buckets 5000 --release process --core auto
ilstack timing-send --stub raw:enp1s0np0 --link-mbps 1000 --delta-unit 1e-8 --slots 64 --target 0.05 --fill 0.95 \
                    --release kernel --rate-limit --ahead 10 --in-flight 10 --core none   # against the FPGA (root, etf queue)
ilstack timing-recv --iface enp66s0np0 --seconds 30                        # counts what the device forwards (root)
```

The sender is the real dispatcher fed with full-size dummy buckets; it
reports where the first slot of each bucket landed (from the syncs' DELTA),
every late start, stall and drop with its cause, and whether each
certificate the interlock issued recomputes from its log, which is the one
loss measure that needs no receiver. Count frames with the NICs' PHY
counters (`ethtool -S`, `tx_packets_phy` / `rx_packets_phy`); a Python
receiver drops at 60 000 frames a second.

Results against the VPK180 on as10 (64 full packets per 1 ms bucket): the pacer process, two hours, 460.8 M frames,
zero loss at the FPGA and 27 packets dropped by the sender, all in episodes
of system activity on the box; the kernel release, first slot at 50-52 µs
for a 50 µs target, lock spread 2 µs, zero loss in partial hours. The
scripts: `deploy/as10-hour.sh` (pacer process), `deploy/as10-edt.sh`
(kernel release), `deploy/as10-e2e.sh` (frontend and compute through the
FPGA with requests and the verifier). Do not log in to the box during a
loss run: a login or a systemd timer costs a few packets.

What the pacer process needs to hold a 1 ms bucket: a core of its own and
SCHED_FIFO (`ILSTACK_LOWJITTER=1` also freezes the collector and locks
memory), a duty cycle under the kernel's 95 % real-time budget (the 50 µs
spin window), the NIC's completion interrupt for its queue steered to
another core, the next buckets already queued (`--in-flight`, `--ahead`),
and a guard at each end of the bucket (`--target 0.05 --fill 0.95`). The
kernel release needs none of that.

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

Then, with the model name as `M` (or without a GPU and with the mock engine:
`sbatch --gpus=0 co.sbatch`):

```
sbatch il.sbatch
sbatch --export=ALL,TOKENIZER=$M,MODEL_NAME=$M fe.sbatch
sbatch --export=ALL,ENGINE=vllm,MODEL=$M co.sbatch
```

From a laptop, `deploy/chat.sh` fetches the API key over ssh, prints the
frontend's health, and turns typed messages into requests through the
ingress (`MODEL=$M` for a real model).

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
