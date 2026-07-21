# Interlock inference CLI app — reference implementation

Full spec: [`../docs/inference-cli-app.md`](../docs/inference-cli-app.md). This dir holds
the runnable reference code: a multi-turn Llama-2-7b chat that runs over the interlock,
with an in-band zero-knowledge proof you can trigger mid-conversation. The model runs, the
proof is generated, and the proof is **verified — all on the Spark**; only small control
messages cross the wire (no out-of-band/WiFi/SSH channel).

| file | role |
|---|---|
| `infcli.py` | client (port 0): multi-turn `chat`, certificate verify, in-band `challenge`, combined cert+proof panel |
| `model_server.py` | server (port 1): greedy `generate()` (HF Llama-2-7b) + in-band ZK router `handle_challenge()` |
| `server_run.sh` | launch the server in one GPU container (model + prover + verifier) |
| `client_run.sh` / `loopback_run.sh` | launch the chat / a scripted one-shot client in a container (Spark loopback) |
| `loopback_demo.py` | non-interactive driver: one prompt → response → challenge → panel |
| `cert_parse.py`, `cert_send_spaced.py`, `zk_challenge_send.py` | cert decode + bring-up senders (verified on silicon) |

The proof itself lives in the `infproof` repo: `model_server.py` shells out to
`infproof/analysis/interlock_challenge.py` (`CHALLENGE_PY`), which proves the
unexplained-information bound, runs the standalone Rust verifier, and binds the proof's
public output token-ids to the certified response.

## How the binding works

The wire payload is **never text** — it is the canonical token-id array (little-endian
uint32). The client tokenizes locally and sends ids; the model only ever sees ids; the
proof runs on ids. So the **same bytes** flow through three independent checks:

1. the interlock **certificate** binds the request/response payload (per-packet HMAC + hash);
2. `generate()` produces the response **from** the request ids (greedy/argmax → U ≈ 0);
3. the **ZK proof** runs on `store[overall_req]`/`store[overall_rsp]` (the certified bytes)
   and reveals the output ids as public constraints.

The client's `/prove` panel shows the certificate and the proof side by side and confirms
`H(local request payload) == H(proof request payload)` (and likewise for the response) — so
the bytes the interlock certified are exactly the bytes the proof ran on.

## Run it — Spark loopback (both interlock ports on the Spark)

Terminal A — server on port 1 (`enP7s7`):
```
bash server_run.sh                       # sound proof (T=80); MAX_NEW_TOKENS=64
CHALLENGE_TQ=4 MAX_NEW_TOKENS=24 bash server_run.sh    # fast/dev while iterating
```

Terminal B — interactive chat on port 0 (`enxb8fbb3b1f53c`):
```
bash client_run.sh enxb8fbb3b1f53c
  you> What is the capital of France?
  bot> Paris. ...
  you> /prove          # in-band ZK proof of the last turn; streams status, prints the panel
  you> /reset          # clear the conversation context
  you> /quit
```

Or a one-shot, non-interactive check:
```
bash loopback_run.sh enxb8fbb3b1f53c
```

## Run it — MacBook client + Spark server

Spark: `bash server_run.sh` (as above). Mac (scapy needs root for `/dev/bpf`; no docker):
```
pip3 install scapy transformers          # transformers = tokenizer only, no torch/GPU

# the Mac runs the tokenizer, not the model — grab just the tokenizer files (~700 KB):
mkdir -p ~/models/llama-2-7b-hf
scp claude@spark-c191:~/llama2-tokenizer.tgz /tmp/ && \
  tar -xzf /tmp/llama2-tokenizer.tgz -C ~/models/llama-2-7b-hf

sudo python3 infcli.py --iface en7 --model ~/models/llama-2-7b-hf chat
```
`--iface en7` is the Mac's interlock-facing Ethernet (check `ifconfig`). The Mac does
**not** run the model. Low-level commands still work: `send --text`, `log`,
`show <rid>`, `verify <rid>`, `challenge <rid>`.

## Hard rule

One packet in flight at a time, spaced (default 300 ms). The per-packet cert HMAC has no
back-pressure; a flood wedges the interlock and needs a power cycle. Both the client and
the server are single-in-flight by construction, and the server spaces its control
replies (`CTL_GAP`) — keep them that way.

## Operational note — interlock reset between sessions

Validated 2026-06-18 over the interlock (multi-turn): "capital of France?" → Paris.,
"And of Germany?" → Berlin. (the request binds the 25-token transcript, so the proof
covers the conversation context), both certs `tau`/`overall` PASS, in-band `/prove`
streaming, Rust verify ACCEPT, combined panel with `H(local)==H(proof)` MATCH, U≈0.

An earlier bitstream stopped issuing certificates after one full session (NICs stayed
healthy) — this traced to a **gateware timing violation**, not the control traffic. It was
fixed by reflashing the corrected bitstream (`top_timingfix.job`), after which `/prove`
re-ran cleanly with no wedge. If instability recurs, reflash a known-good job.

To reset, reflash the FPGA (~5 min) — the flash runs in the x86 QEMU VM, so use the
namespace-crossing helper, then re-run `server_run.sh`. Full procedure:
[`../docs/flashing-the-interlock.md`](../docs/flashing-the-interlock.md).
```
bash ~/flash_drive.sh launch && bash ~/flash_drive.sh wait   # "Chain programming PASSED"
```
If the bridge proves sensitive to the streamed progress, thin the in-band control traffic
with `CHALLENGE_STATUS_SECS` (server env, default 15 s) — fewer status packets per proof.
The proof always streams start/`a,b OK`/done plus the final RESULT regardless.

## Deployment note

The server is one `nvcr.io/nvidia/pytorch:25.11-py3` container with `--gpus all`
(generate + prove + verify), `--network host`, and `--cap-add NET_RAW --cap-add NET_ADMIN`
(raw L2 + promiscuous). Validated on the DGX Spark (GB10, sm_121): the CUDA-JIT prover and
the host-built Rust verifier both run inside this image.
