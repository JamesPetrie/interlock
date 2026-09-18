# Interlock Prototype Plan

```
TODO (James): a plan does not belong in docs/. Merge what is still relevant
(e.g. the "still simulated / deferred" points) into the README or another
suitable place, and delete this file. Also, do we still need the "prototype" dir?
```

Bring the verification protocol up end-to-end on real hardware in stages, each of which makes exactly one property *real* (and leaves the rest simulated until a later stage). Companion to `verification-protocol.md` (the spec) and `model/` (the executable reference the software stages reuse).

## Topology: single-machine loop

Both Spark ethernet ports cable into the FPGA, so traffic leaves the Spark, passes through the interlock, and comes back — with the prover frontend and the inference compute running as **separate processes on the same Spark**:

```
  Management:  WiFi ──────────────── Tailscale / SSH   (untouched)

  Spark
  ┌────────────────────────────────────────────────┐
  │  netns "fe"                    netns "compute"   │
  │  frontend process              compute process   │
  │  (external switch,             (Llama-7B,        │
  │   chat, log)                    inference)        │
  │     │                                │           │
  │   built-in eth                   USB-C eth        │
  └─────┼────────────────────────────────┼───────────┘
        │                                │
     port 1                          port 2
        └──────────  FPGA (interlock)  ───┘
```

- **Management on WiFi.** Tailscale rides WiFi and never touches the ethernet NICs, so you can misconfigure the loop arbitrarily and still SSH in to fix it.
- **Two NICs into the FPGA.** Built-in ethernet → port 1, USB-C ethernet → port 2. Speed mismatch is irrelevant at prototype rates.
- **Network namespaces force traffic through the FPGA.** Two local NICs on one host would otherwise be shortcut in-kernel and never hit the wire — certifying nothing. Putting each NIC in its own netns means the peer IP isn't local, so the kernel must route out the physical port. (Raw ethernet in V3 retires this — you pick the egress interface explicitly.)

This loop exercises the interlock more faithfully than a linear MacBook→FPGA→Spark chain: both ports are live, requests one way and responses the other — the bidirectional traffic the interlock actually has to certify.

### Setup checklist (V1/V2)
- Move each ethernet NIC into its namespace: `ip link set <iface> netns fe|compute`.
- Assign an IP per namespace; add **static ARP** (`ip neigh add`) for the peer so IP doesn't depend on ARP resolving across the (frame-reoriginating) bridge.
- Confirm the default route and DNS ride WiFi (`ip route`, `/etc/resolv.conf`) before pulling the ethernet NICs into namespaces.

## What becomes real at each stage

| Stage | Transport | Cert generation | Newly real | Still simulated |
|---|---|---|---|---|
| V1 | TCP socket | none | the data path | all trust |
| V2 | TCP socket | software (model) | cert/challenge *logic* | trust boundary, timing |
| V2.5 | TCP socket | software on FPGA CPU | trust boundary (cert gen off-host) | timing, isolation |
| V3a | raw ethernet | software | wire format, timing/buckets | unforgeable certs, isolation |
| V3b | raw ethernet | gateware (SHA/HMAC) | unforgeable certs | isolation |
| V4 | raw ethernet | gateware | no-leak isolation | physical separation |

The single most useful discipline here: each rung makes **one** thing real. It's easy to feel V2 "works" and think you've shown something secure — you've shown the logic is correct, which is necessary and worth doing first, but no trust property holds until V3.

## Stages

**V1 — dataflow.** A CLI chat in the frontend process sends a request over a length-prefixed TCP socket through the FPGA to the compute process, which runs Llama-7B and returns a response; the chat prints it. The FPGA is the existing dual-CoreTSE sanitizing bridge — no cert logic yet. Goal: the pipe works end to end through the interlock.

> *Transport note:* length-prefixed TCP, not SCP. One persistent connection (no per-message SSH handshake), no sshd/keys/file-polling in the namespaces, and the framing you write — `request_id`, `reference_id`, `length`, payload — is the framing you keep into V3. SCP's file framing maps onto nothing and is throwaway; reserve it at most for a one-off "did a byte cross the FPGA" smoke test.

**V2 — certificate + challenge logic (software).** Wire in the node prototype (`prototype/`: `wire.py` + `compute.py` / `interlock.py` / `frontend.py` / `verifier.py`): the frontend logs every packet, the interlock emits one certificate per turn, and a separate *verifier* process opens and verifies a challenged packet. Close the loop — run the verification on real traffic and confirm the negative tests fire (tamper a logged byte → verification fails). The verifier is its own process so the roles stay honest even in software. Real: the cert/challenge logic. Simulated: the trust boundary (cert gen is prover-side software) and timing (TCP has no 1 ms buckets, so certs commit content and order, not timing).

**Explanation step — recomputation or ZKP (later, after the dataflow/cert prototype).** The prototype above stops at "this response is the committed one." Turning an opened response into an unexplained-information bound U is a separate, later addition, via either recomputation (stage the opened pair into a recomputation node that shares no state with the workload compute, commit-then-reveal score → U) or a zero-knowledge proof. Headline milestone for that step: **challenge → open → explain → U**, honest ≈ 0 vs a corrupted response high. Out of scope for the current dataflow/certificate roadmap.

**V2.5 — cert software on the FPGA CPU (optional).** If the FPGA part has a processor (PolarFire SoC / Zynq / Kria do; a plain MPF300 does not), run the cert software on it. This makes the trust boundary *real* — cert generation physically on the interlock, separate from the Spark — with software flexibility, turning V3b from "move to gateware" into the smaller "move from FPGA-CPU to FPGA-fabric." The `interlock.py` node can run almost verbatim here.

**V3a — custom ethernet transport.** Replace TCP with raw L2 frames in the wire format; FPGA still bridging + sanitizing (reuse the existing deframe/reframe/FCS path); certs still in software. Raw frames also retire netns + static ARP (you choose the egress interface and set the destination MAC). Add payload encryption here (AES-CTR, position-addressable as the recomputation scoring needs). Real: the wire format; bucket/timing structure becomes meaningful for the first time.

**V3b — certificates in gateware.** Move SHA-256/HMAC certificate generation into fabric, and add the verifier-nonce latch (recency). Deliberately separated from V3a: the crypto gateware is the hard, silent-wrong-results-prone part, and shouldn't be debugged on top of a freshly changed transport. Real: unforgeable certificates — the trust boundary in hardware.

**V4 — logical isolation.** Ping-pong buffer with non-interacting read/write planes, so batch timing is clock-driven and there's no data-dependent control-plane coupling. Real: the interlock's no-leak guarantee.

## What stays simulated even at V4

Frontend, compute, and the recomputation node all share one Spark OS, so they *could* communicate through loopback or shared memory, bypassing the FPGA. This prototype validates dataflow, protocol, certificates, and gateware — but the **isolation/trust property only becomes real when compute (and, for Option 1, the recomputation node) move onto physically separate boxes / into an enclosure.** That box-separation is the step beyond this single-Spark prototype.

## Deferred (not in V1–V4)
- Size/timing-distribution accounting.
- Reference chains / multi-part exchanges in the model (single pairs first).
- Physical box separation and the recomputation enclosure (real Option 1 isolation).
- Side-channel hardening (constant-time crypto, power/EM isolation, data diode).
