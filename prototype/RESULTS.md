# Prototype run results

## Consolidated node-per-process — real Llama-7B across the FPGA (2026-06-14)

The current prototype (`prototype/{wire,compute,interlock,frontend,verifier}.py`)
run with each node as its own process and the workload genuinely crossing the
FPGA: `compute.py` (real Llama-2-7B) on the Spark host (built-in NIC, 10.10.10.2),
and `interlock.py` + `verifier.py` + `frontend.py` in a macvlan container on the
USB-NIC side (10.10.10.3) via `run_clients.sh`, so the client↔compute legs go out
the USB port → FPGA (passthrough) → built-in port.

```
you> What is the capital of France?
llama> Paris is the capital of France.          [packet #1]
you> And what about Germany?
llama> Berlin is the capital of Germany.        [packet #2]   (multi-turn context)
you> /challenge 1
  packet #1 VERIFIED — it is the committed response to request #1
=== FPGA traversal (client<->compute legs): usb_tx +14  enP7s7_rx +14 ===
```

Counters match exactly, so every request/response and the challenge crossed the
interlock hardware. (Recomputation / U is descoped — the verifier confirms the
response is the committed one; turning it into a U bound is a later step.)

Operational note: forwarding had to be recovered first. After a week of the box
being heavily used, the FPGA showed carrier on both links but forwarded nothing.
A JTAG reflash (`PROGRAM PASSED`) and an ISC re-init (`set_programming_action
-name {MPF300TS} -action DEVICE_INFO`) both passed but did NOT fix it — the
wedged part was the external VSC8575 PHY, which JTAG can't power-cycle. A
**physical power cycle of the eval board** restored forwarding immediately. The
flash config is non-volatile and was fine throughout. (See `bitstreams/MANIFEST.txt`.)

---

> Below: historical record from the earlier (pre-consolidation) layout. The
> prototype was later consolidated to node-per-process and the recomputation / U
> measurement descoped to a later step (recomputation or ZKP). The U figures were
> a real measurement at the time, via the now-removed `run_demo.py`; file names
> like `compute_server.py`/`run_demo.py` refer to that earlier layout.

Captured run of the V1 / V2 / recomputation milestones on the Spark
(`spark-c191`, aarch64, Llama-2-7B, `venv-hf` torch 2.13/cu130, transformers
5.8). Single-host over loopback TCP; see *FPGA / MacBook path* below for why the
physical FPGA loop wasn't exercised this run.

## Transcript

```
=== V1 dataflow ===
prompt           : 'Q: What is the capital of France?\nA:'
response (text)  : 'Paris is the capital of France.\nQ: What is the capital of France?\nA: Paris is the capital'
response (tokens): 24 ids

=== V2 certificate + challenge ===
certificate bytes: 140 ; anchored current bucket: 20
challenge (bucket 17, byte 0): VERIFIED, bound request_id=1
tampered packet: REJECTED (AssertionError) as expected

=== recomputation: unexplained information U ===
U(honest response)    =   0.035 bits  (0 of 24 tokens unexplained)
U(5 tokens corrupted) =   174.5 bits  (7 of 24 tokens unexplained)
ratio                 = 5039x
```

## Reading the results

- **V1** — a request crossed a TCP socket to the compute node, which ran
  Llama-2-7B and returned a correct answer ("Paris is the capital of France",
  then greedy repetition — expected for 24 greedy tokens with no chat template).
  The pipe works end to end.
- **V2** — the request/response traffic produced a 140-byte HMAC certificate;
  a randomly chosen challenge opened against it and verified, bound to the
  originating request; flipping one logged response byte made verification fail.
- **recomputation** — `U(honest) = 0.035 bits` is exactly 24 × −log₂(0.999):
  every token matched the recomputation's greedy prediction, so 0 of 24
  positions are unexplained — the "deterministic model → ~100% explained" case.
  Corrupting 5 tokens drives `U` to 174.5 bits over 7 unexplained positions
  (corruption propagates: the teacher-forced prefix is wrong for ~2 positions
  past the 5 edits). A 5000× separation between honest and corrupted output —
  the core P3 measurement, on a real model.

## What is real vs. simulated (per `docs/prototype-plan.md`)

Real: the inference dataflow, the certificate/challenge logic, and the U
measurement — on real hardware and a real model. Simulated by design at this
stage: loopback TCP stands in for the netns + FPGA loop; certificate generation
is software (no hardware trust boundary yet); payloads are plaintext (encryption
is V3); one OS hosts everything (isolation not real); decoding is greedy
(hardware-nondeterminism noise model deferred).

## RESOLVED (2026-06-14): passthrough flashed, prototype runs over the FPGA

Built the passthrough bitstream (commit `7e87750` — transparent bidirectional
cross-wire `fabric_bridge`, forwards all Ethernet frames) on the Hetzner Libero
box and flashed it via the `box64test` FlashPro-in-qemu rig: **PROGRAM PASSED**
(2026-06-14 13:00). The first attempt failed because another job was using
~113 GB (all RAM + swap), OOM-thrashing the programming VM mid-write and leaving
the FPGA links down; it completed cleanly once that memory was freed.

**Forwarding now works.** The same test that failed below now passes — a
container whose only NIC is the USB port pings the host on the built-in port
through the FPGA: 4/4, 0% loss, ARP resolves with no static hack,
`usb_tx +8 / enP7s7_rx +8`.

**The full prototype then ran over the real link:** client in a macvlan
container (`10.10.10.3`) → FPGA → `compute_server.py` (Llama-2-7B) on the host
(`10.10.10.2`). Same results as loopback — cert verified, tamper rejected,
`U(honest)=0.035` vs `U(corrupted)=174.5` bits. V1 dataflow genuinely traversed
the interlock hardware.

Bitstream archived: `/root/fpga/bitstreams/passthrough-7e87750.job` (Hetzner) and
`~/fpe/top.job.passthrough` (Spark), sha256 `dce14135…`; prior deframe image
backed up at `~/fpe/top.job.bak-20260614`. (Note: the passthrough image is a
transparent wire — it does *not* do the interlock certificate logic, which stays
in software; gateware cert logic is the later V3b step.)

The diagnostic that led here is preserved below.

## FPGA forwarding test (2026-06-14)

Attempted to route the traffic through the FPGA from the single Spark, using the
`docker` group to get the network privileges a non-root account lacks: a macvlan
network over the USB NIC (`enxb8fbb3b1f53c`) with the lightweight (stdlib-only)
**client run in a container whose only interface is that NIC** — so its packets
to the host's `10.10.10.2` (on the *other* NIC, `enP7s7`) have no path except out
the USB port → FPGA → back in the built-in port. Compute would stay on the host.

Result: **generic Ethernet frames do not cross the current FPGA image.** Measured
with interface packet counters:

| Test | usb_tx (left USB NIC) | enP7s7_rx (arrived at built-in NIC) |
|---|---|---|
| broadcast ARP (no static ARP) | +6 | **+0** |
| unicast ICMP (static ARP both sides) | +6 | **+0** |

The OS-level loop is provably correct — frames *do* egress the USB NIC — but
nothing arrives on the built-in NIC, for either broadcast or unicast. So the
block is in the gateware, not the host: the currently flashed deframe/reframe
image does not transparently forward generic IP/Ethernet between its two ports.
This is consistent with the gateware extracting a length field and reframing — it
likely forwards only its specific packet format, not arbitrary Ethernet II frames
(EtherType 0x0800/0x0806), which get dropped.

Interface state (hardware is cabled, links up):

- `enP7s7` (built-in eth) = **10.10.10.2/24**, UP/carrier — one FPGA port.
- `enxb8fbb3b1f53c` (USB-C eth) = UP/carrier, no IP — the other FPGA port.
- `wlP9s9` (WiFi) = default route + Tailscale — management.

### To actually traverse the FPGA, one of

1. **Flash a transparent passthrough image** that forwards all Ethernet frames
   byte-for-byte between the two CoreTSE ports — then IP/TCP rides through and the
   prototype runs over the real link unchanged.
2. **Speak the V3 custom packet format** the current image expects, instead of IP
   (a raw-`AF_PACKET` sender/receiver matching the gateware's framing).
3. **Verify cabling / port mapping** — that the two Spark NICs land on the FPGA's
   actual bridged port pair.

### MacBook path (once forwarding works)

Code is already drop-in (`COMPUTE_HOST` / `COMPUTE_BIND`, connect-or-spawn): run
`compute_server.py` on the Spark (reachable at 10.10.10.2), plug the MacBook into
the FPGA's free port with a 10.10.10.x IP, and run
`COMPUTE_HOST=10.10.10.2 python3 run_demo.py`. The MacBook client is pure Python
stdlib — no torch. Two real hosts sidesteps the single-host netns/root issue, but
still needs the gateware forwarding fix above.
