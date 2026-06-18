# Interlock inference CLI app — reference implementation

Full spec: [`../docs/inference-cli-app.md`](../docs/inference-cli-app.md). This dir
holds the runnable reference code.

| file | role | status |
|---|---|---|
| `infcli.py` | MacBook **port-0 driver** (#3): drive requests, capture + verify certs, `challenge` **in-band** | logic validated vs. real cert hash; scapy transport untested on macOS |
| `model_server.py` | Spark **port-1 I/O** (#2) **+ in-band ZK control router**: inference → `generate()`, ZK control → `handle_challenge()` | **verified end-to-end on silicon (loopback)** |
| `zk_challenge_send.py` | sends one in-band CHALLENGE control packet (bring-up/test) | verified on silicon |
| `cert_send_spaced.py` | spaced one-at-a-time packet sender (bring-up) | verified on silicon |
| `cert_parse.py` | certificate decoder + verifier (`tau` HMAC + `overall` hash) | verified on silicon (6/6) |

Remaining: `challenge.py` (ZKP side, infproof — see spec §6.3) and a real `generate()`
(model agent) replacing the echo stub.

## Quick start

MacBook (port 0):
```
pip install scapy
sudo python3 infcli.py --iface en7 send --text "hello"
sudo python3 infcli.py --iface en7 log
sudo python3 infcli.py --iface en7 challenge 0    # in-band over the interlock; no WiFi/SSH
```

Spark (port 1), in a `NET_RAW`+`NET_ADMIN` container (promiscuous mode required):
```
docker run --rm --network host --cap-add NET_RAW --cap-add NET_ADMIN -v $PWD:/app \
  python:3-slim python3 /app/model_server.py enP7s7
```

## Hard rule

One packet in flight at a time, spaced (default 300 ms). The per-packet cert HMAC has
no back-pressure; a flood wedges the interlock and needs a power cycle. Both the sender
and the model server are single-in-flight by construction — keep them that way.
