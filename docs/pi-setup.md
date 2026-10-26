# Raspberry Pi setup for the full demo flow

This guide takes a stock Raspberry Pi to the point where it plays the
**prover-frontend** role in the demo topology: sending inference requests
through the interlock, receiving responses and the once-per-second
certificates, logging all traffic, and answering verifier challenges.

```
 Pi (client + logger + challenge responder)          Spark (Llama inference)
        │                                                     │
        └── eth0 ──▶ [ port 0 │ interlock FPGA │ port 1 ] ◀── enP7s7
                            MPF300-EVAL-KIT
```

Port 0 is the prover/verifier-facing port (certificates egress here); port 1
faces the quarantined compute node. One ethernet cable per path.

**Read first:** [`interlock-overview.md`](interlock-overview.md) (what the
device does), then [`verification-protocol.md`](verification-protocol.md)
(the log/certificate/challenge spec the Pi implements the client half of).

## 0. Honest status — what runs today vs. what needs building

| Demo step | State |
|---|---|
| Flash the board, verify links | works today (not done from the Pi — see §3.1) |
| Passive sanity: syncs + certs visible on the Pi port | works today (§3.3) |
| Bucket-timing calibration from the Pi | works today (§3.4) — **do this first, it decides everything else** |
| Bucket-timed sends accepted through the device | works today via `bench/burst_test.py` (§3.5) |
| Cert receive + HMAC verify on the Pi | works today via `app/cert_parse.py` (§3.6) |
| Inference request/response roundtrip | **needs the send-path port**: the June client (`app/infcli.py`) speaks the pre-bucket wire format and is dropped by the prod-1ms build. Port its sends onto `burst_test.py`'s bucket-timed sender (client and server side). |
| Complete traffic logs, byte-audited against certs | **not implemented** — spec in `verification-protocol.md` (log format + byte-audit) |
| Challenge responder (cert + log slice) | **not implemented** — spec in `verification-protocol.md` (challenge/opening) |

## 1. Hardware

- Raspberry Pi 4 or 5 with the built-in gigabit port, running 64-bit
  Raspberry Pi OS. (The GbE MAC is a real 1000BASE-T PHY — it links to the
  eval kit directly, no switch needed.)
- Storage: the SD card is fine for bring-up; for the "complete logs"
  milestone use a USB-attached SSD (a day of 100 Mb/s traffic ≈ 1 TB at
  line rate, but demo traffic is far below that — 32 GB is plenty for a
  demo session).
- One cable Pi `eth0` → board **port 0**; one cable Spark → board **port 1**.
- The board is flashed and powered per
  [`flashing-and-testing.md`](flashing-and-testing.md) — flashing is *not*
  done from the Pi.

## 2. One-time software setup (needs sudo once)

Mirror the Spark bench pattern — Docker with capability grants, so nothing
after setup needs interactive root:

```bash
sudo apt update && sudo apt install -y docker.io tcpdump git
sudo usermod -aG docker $USER    # log out/in to take effect
docker pull python:3-slim
```

Get the repo (the Pi needs a read-scoped token for the private repo):

```bash
git clone https://github.com/JamesPetrie/interlock.git ~/interlock
mkdir -p ~/fpe && cp ~/interlock/bench/*.py ~/interlock/bench/*.sh ~/fpe/
```

Also copy `app/` from the `docs/inference-cli-app` branch (client +
cert tools):

```bash
git -C ~/interlock fetch origin docs/inference-cli-app
git -C ~/interlock show origin/docs/inference-cli-app:app/infcli.py     > ~/fpe/infcli.py
git -C ~/interlock show origin/docs/inference-cli-app:app/cert_parse.py > ~/fpe/cert_parse.py
```

For the chat client the Pi also needs the Llama **tokenizer files only**
(a few MB — token↔text conversion is client-side; weights stay on the
Spark). Copy the `tokenizer*` files from the checkpoint into
`~/models/<model>/`.

No Microchip software or license is ever needed on the Pi.

## 3. Bring-up, in order

Each step gates the next. Interface is assumed `eth0`; substitute yours.

### 3.1 Board flashed, link up

Flash `prod-1ms` (prebuilt `.job` on the
[Releases page](https://github.com/JamesPetrie/interlock/releases)) per
`flashing-and-testing.md`. On the Pi, `ip link show eth0` should show the
link up at 1000 Mb/s once cabled.

### 3.2 Passive sanity (~1 minute, proves the whole egress path)

The device emits traffic on its own: sync frames every bucket (1 kHz) and a
certificate every second on port 0.

```bash
sudo tcpdump -i eth0 -c 20 -XX
```

Expect a steady stream of small frames. No traffic at all ⇒ wrong port,
dead link, or the board isn't running the expected bitstream — stop and fix
before anything else.

### 3.3 Cert receive + verify

```bash
docker run --rm --network host --cap-add NET_RAW \
  -v ~/fpe:/fpe python:3-slim \
  bash -c "pip install -q scapy && python3 /fpe/cert_parse.py eth0"
```

PASS = certs arrive at 1 Hz, `tau == HMAC-SHA256(key, m)` verifies, and
`bucket_start` advances by 1000 per cert. The demo key is the app's
`DEFAULT_KEY`; in any real deployment the key is provisioned per
`security-architecture.md`.

### 3.4 Timing calibration — the critical measurement

The prod-1ms build accepts a frame only if it *physically arrives during
the bucket it declares*. The Spark achieves a 9 µs landing-error std with
the flywheel servo; **the Pi's NIC is unproven**. Measure before building
anything on top:

```bash
docker run --rm --network host --cap-add NET_RAW --cap-add SYS_NICE \
  -v ~/fpe:/fpe python:3-slim \
  bash -c "pip install -q scapy && python3 /fpe/calib_probe.py eth0 300 0.5 --tuned"
```

- **PASS:** ~0% miss, landing-error p99 well under ~300 µs → 1 ms buckets
  are viable from the Pi; continue as-is.
- **MARGINAL/FAIL:** late-tail jitter approaching the 1 ms period → build
  and flash the **100 ms** bucket variant (`BUCKET_MS=100` on the
  `feature/build-config-knobs` branch) for the demo. Everything else in
  this guide is unchanged; only the timing margin (and cert cadence
  constants) differ.

Known pitfalls (details in [`../bench/README.md`](../bench/README.md)):
send as *early* in the bucket as possible (there is no guard window); one
RT process per core; the settle stage fails spuriously ~40% of the time —
re-run.

### 3.5 Active accept-path test

```bash
docker run --rm --network host --cap-add NET_RAW --cap-add SYS_NICE \
  -v ~/fpe:/fpe python:3-slim \
  bash -c "pip install -q scapy && python3 /fpe/burst_test.py eth0 eth0 30 0.2 --req"
```

The Pi sends the **REQ** direction, so `--req` is required (request ids
must be globally monotonic across runs — the device's `prev_id` never
resets, and a fresh run reusing low ids is silently 100% rejected).
Acceptance is counted on the Spark side (`far_iface` there); coordinate
with a `burst_test.py` listener on the Spark, or read acceptance from the
Spark NIC's rx counters. Expect near-zero drops at util 0.2.

### 3.6 Inference roundtrip (after the send-path port)

Once `infcli.py`'s send path is ported to the bucket-timed canonical
format (§0):

- Spark: `bash server_run.sh` (model server on port 1; for a fast in-band
  proof later, point it at Llama-3.2-1B and VerInf).
- Pi:

```bash
docker run --rm -it --network host --cap-add NET_RAW --cap-add NET_ADMIN \
  -v ~/fpe:/fpe -v ~/models:/models python:3-slim \
  bash -c "pip install -q scapy transformers && \
           python3 /fpe/infcli.py --iface eth0 --model /models/llama chat"
```

Type a prompt; the reply plus its per-packet certificates should render,
with `tau` verified locally.

### 3.7 Traffic logging (to build)

A capture service on `eth0` (pcap ring buffer), indexing packets by bucket
number per [`bucket-declaration-spec.md`](bucket-declaration-spec.md)
(packets are self-locating — the declared bucket in the canonical header
is authoritative, never the Pi's own clock), and **byte-auditing each
arriving cert**: recompute the record → bucket → epoch hash hierarchy
(`traffic_commit.md`, `cert_build.md`) from the capture and compare. A
mismatch within one second means a capture gap *now*, not a mystery at
challenge time.

### 3.8 Challenge responder (to build)

Per `verification-protocol.md`: given a sampled (bucket, byte) challenge,
return the certificate plus the minimal log slice (one bucket-hash list,
one bucket's records, one packet header + payload hash, ~100 KB) whose
recomputed hashes match the cert. Negative tests matter as much as the
happy path: a tampered log must fail to verify; a missing bucket must
produce an honest failure (charged as unexplained information), not a
crash.

## 4. The full demo flow (end state)

1. Pi and Spark both link through the interlock; certs tick at 1 Hz on the
   Pi, logged and byte-audited continuously (§3.7).
2. Operator types a prompt on the Pi → token ids cross as bucket-timed
   canonical packets → Llama on the Spark generates → response ids return
   → Pi detokenizes and displays, showing the certs binding both payloads.
3. A verifier challenge arrives (or is simulated locally) → the Pi answers
   with the certificate and the matching log slice (§3.8); the verifier
   recomputes every hash.
4. Optionally, an in-band ZK challenge asks the Spark to *prove* the
   response (VerInf; ~2¼ min for Llama-3.2-1B), and the Pi's panel confirms
   the proof ran on exactly the certified bytes.
