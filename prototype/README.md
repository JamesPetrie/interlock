# Prototype — inference-time verification (node-per-process)

Runnable prototype of the certificate/challenge dataflow from
`../docs/verification-protocol.md`: a multi-turn Llama conversation where every
request/response is committed by the interlock, and any past response can be
challenged and verified as the committed one. Each design node is its own
process; the shared wire format lives in one place.

**Scope:** the inference-time verification *dataflow + certificate/challenge*.
Turning a challenged response into an unexplained-information bound U
(recomputation or a ZKP) is a later step and is **not** in this prototype.

## Files → diagram nodes

| File | Diagram node | Role |
|---|---|---|
| `wire.py` | — (shared) | the wire format + hashing every node agrees on byte-for-byte |
| `compute.py` | **Prover compute** | runs the LLM: request packet → response packet |
| `interlock.py` | **Verifier interlock** | in the data path: hashes packets, emits the per-turn certificate |
| `frontend.py` | **Prover frontend** | the log + challenge openings; the interactive chat CLI |
| `verifier.py` | **External verifier** | checks the opening — the response is the committed one |
| `common.py`, `transport.py` | — | ports, shared secret, TCP framing, (de)serialization |
| `test_protocol.py` | — | cert/challenge tests (honest path + one negative per check) |

Each node file holds its own logic (the `Interlock` / `Frontend` / `Verifier`
classes) and is also the process entry point; `wire.py` is the only shared
import, so the format can't drift between nodes.

## Wiring

```
data path:   frontend ──► interlock ──► compute
challenge:   frontend ──► verifier
```

`frontend` is a pure client; `compute`/`interlock`/`verifier` are servers
(`interlock` is also a client to `compute`). The certificate is produced *in the
data path* by `interlock` and stored by `frontend`.

## Run — single host (loopback)

```
MOCK=1 ./run_all.sh        # wiring test, no Llama (canned compute)
./run_all.sh               # real Llama-2-7B, all four nodes on this host
python3 test_protocol.py   # cert/challenge unit tests (no Llama, no servers)
```

Then chat; `/challenge [id]` verifies a past response is the committed one; `/cert [id]` prints the certificate committing it;
`/list`; `/quit`.

## Run — over the FPGA

compute on the host's built-in-NIC side, the client nodes in a macvlan container
on the USB side, so their calls to compute cross the FPGA:

```
# host (built-in NIC = 10.10.10.2), with torch:
cd ~/interlock-proto/prototype && ~/venv-hf/bin/python compute.py

# client nodes in a container on the USB-NIC side:
docker run -it --rm --network fpga_net --ip 10.10.10.3 -e COMPUTE_HOST=10.10.10.2 \
  -e PYTHONDONTWRITEBYTECODE=1 -v ~/interlock-proto:/proto python:3-slim \
  bash /proto/prototype/run_clients.sh
```

The FPGA is the transparent passthrough wire on the compute legs; `interlock.py`
is the logical interlock doing the certificate logic in software — moving that
into the gateware is the later V3b step.
