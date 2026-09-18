# The Interlock: a Minimal Trusted Device for Auditing AI Compute Traffic

*Draft overview — motivation, architecture, and target security properties.*

## 1. Motivation

A growing class of agreements — between AI developers and auditors, between
companies, or between states — requires one party (the **prover**) to convince
another (the **verifier**) of claims about what its AI systems are doing,
without exposing the models, inputs, or customers involved. A basic building
block for such claims is an auditable record of *what information enters and
leaves a compute node*. If the verifier can bound the information flow across a
node's network boundary, it can bound what the node could have disclosed,
coordinated, or been commanded to do — and downstream protocols can then check
that the traffic is *explained* by a declared computation (e.g., inference on a
declared model).

Trusting the prover's own software stack for this record is circular: the
stack is exactly what is being audited. The interlock takes the opposite
approach: a **physically minimal, logically small trusted device** — an FPGA —
is placed in-line on the *only* network path of a quarantined compute node.
Every byte crossing the boundary passes through it. The device does three
things and nothing else:

1. **Sanitizes** traffic so that no information can cross outside the
   committed packet contents (no header covert channels, no sub-millisecond
   timing channels);
2. **Commits** to every packet in a hash hierarchy, folded once per second
   into a **traffic certificate** authenticated by an on-device HMAC key
   shared only with the verifier;
3. **Enforces** a small set of validity rules that make the committed log
   unambiguous and cheaply auditable by random sampling.

The prover keeps the actual traffic logs and answers challenges; the device
stores no history. The certificate is the sole authority: any challenge the
prover's records cannot answer consistently with the certificates is charged
against the prover as unexplained information. Prover failures therefore
degrade the prover's standing, never the soundness of the verifier's bound.

The trusted computing base is deliberately tiny — a dozen or so SystemVerilog
blocks, no CPU, no firmware, no software stack on the trusted path (the build's
Mi-V soft core only brings up the PHYs and MACs, off the data path). This is
what makes the device a natural target for **formal verification**: the
security of the whole scheme reduces to trace-level properties of a small,
fully inspectable RTL design plus standard cryptographic assumptions.

## 2. Architecture and current implementation

**Physical layout.** The device is a Microchip PolarFire MPF300TS FPGA
(MPF300-EVAL-KIT for the prototype) with two 1G Ethernet ports: one faces the
prover's frontend, one faces the quarantined compute node. The device is the
only Layer-2 endpoint either side sees.

```
verifier ↔ prover frontend ↔ [ port 0 | FPGA fabric | port 1 ] ↔ compute node
                                       interlock                 (quarantined)
```

**Datapath (all fabric, 80 MHz, no CPU on the data path).** In order:

- **802.3 sanitization** (`eth_deframe`/`eth_reframe`): each port terminates
  frames and re-originates them. MAC addresses are forced to fixed values, the
  LENGTH field is enforced against bytes actually received, padding is
  stripped or overwritten, and the FCS is discarded and recomputed. Only the
  canonical payload crosses, verbatim.
- **Canonical-packet checks** (`canon_proc` + gate): all traffic is structured
  as packets with a fixed 64-byte cleartext header (length, time-bucket, id,
  reference, key-commitment) and an end-to-end-encrypted payload the device
  never decrypts. The device enforces: length in range, ids strictly
  increasing per direction, the declared bucket exactly matching the device's
  own bucket counter, reserved fields zero. Violating packets are silently
  dropped — no drop counters, no status frames, no observable side effects.
- **Commitment pipeline** (`traffic_commit`): per packet, a record
  `length ‖ SHA256(header ‖ H(payload))`; per 1 ms bucket, a running SHA-256
  over the bucket's records; per epoch (1 s = 1000 buckets), a running SHA-256
  over the bucket hashes, one per direction.
- **Timing quantization** (`batch_buffer`): egress in each direction is staged
  through a ping-pong buffer that swaps banks exactly on the 1 ms tick, so the
  outside world observes only bucket-granular release times, never sub-bucket
  timing.
- **Certificate builder** (`cert_build` + HMAC-SHA-256): once per epoch the
  device emits `m ‖ HMAC_k(m)`, where `m` is the certificate body defined in
  `verification-protocol.md` (*Certificate packet*). Certificates form a
  hash chain that tiles the bucket timeline; the nonce anchors it to the
  verifier's wall clock. The device generates MACs but never verifies one.

Requests are committed **before** the egress buffer (binding what entered the
quarantine); responses are committed **after** it (binding exactly the bytes
that left). Every value in a certificate is recomputed by the device from wire
bytes — no prover-supplied value enters it — and every field is exactly
predictable by the prover, so the device itself has essentially no covert
output channel.

**Surrounding protocol.** The untrusted prover frontend logs all traffic and
certificates (~30 days). The verifier periodically anchors the chain with a
fresh nonce, then challenges uniformly random (bucket, byte) positions; the
prover opens the minimal log slice (one certificate, one bucket-hash list, one
bucket's records, one packet header + payload hash — ~100 KB, no payloads),
and the verifier recomputes every hash. A separate *recomputation* step —
either a zero-knowledge proof of the inference computation or re-execution
behind a second, simpler interlock guarding a verifier enclosure — then bounds
the unexplained information U in the sampled response. Full details in
`verification-protocol.md` and `security-architecture.md`.

**Implementation status.** The production core on `main` — sanitization,
canonical-packet enforcement, ping-pong timing isolation, chained
certificates, and the recomputation sibling — builds for the eval kit; real LLM traffic has been run across the device.
Deliberately open engineering items: key custody in hardware (PUF-wrapped key
and a persisted monotonic epoch counter in secure NVM — currently the RTL
uses a hardcoded key), DPA-resistant HMAC via the on-die cryptoprocessor,
a build-level gate excluding all debug taps from production bitstreams, and
on-silicon verification of the timing-release behavior.

## 3. Security properties

```
TODO (James): three overlapping property lists exist — S1–S6 here, P1–P5 in
verification-protocol.md, C1–C7/D1–D5 in security-architecture.md.
Please algin/deduplicate these.
```

The scheme's soundness reduces to the following properties. Each is stated as
the property of the device (or device + protocol) that a verified
implementation would establish; the associated RTL mechanism is noted.

**S1 — Information isolation.** No information leaves the quarantine other
than the contents of the egress buffer at bucket changeover points — plus
bucket-granular timing, which is itself committed and capacity-bounded. Every
802.3 field is forced or checked; invalid packets vanish without observable
effect; the device's own emissions (forwarded frames, certificates, per-tick
sync packets routed back to each direction's *own* sender) are
prover-predictable. *Mechanism: `eth_deframe`/`eth_reframe`, `canon_proc`,
`batch_buffer`.*

**S2 — Certificate completeness and information binding.** Every byte that
crosses is committed: the certificate for an epoch binds, via the
record → bucket → epoch hash hierarchy, every packet accepted in that epoch,
in both directions. Conversely the validity rules make packets
*self-locating* — the declared bucket must exact-match true arrival time, and
ids are strictly monotonic per direction per bucket — so the unordered set of
packet contents determines the full ordered log. There is no residual degree
of freedom (ordering, placement, unlogged observables) that could carry
information missed by per-packet accounting.

**S3 — Certificate authenticity.** Only the device (and the verifier) can
produce a valid tag: certificates are HMAC-SHA-256 under a key that at rest
exists only PUF-wrapped in authenticated secure NVM, with tamper detection
escalating to zeroization. Every certificate field is recomputed from wire
bytes; no prover input is ever incorporated.

**S4 — Certificate uniqueness (reset resistance).** Over the lifetime of a
key, at most one certificate is ever generated per epoch. Restarts resume
from a persisted monotonic counter with a skip-ahead margin, so a power cycle
skips epochs but can never repeat one; the gap is visible to the verifier as
unattested time. History cannot be forked or rewound.

**S5 — Timing guarantees.** Certificates are produced in strictly increasing
epoch order and chain via the previous tag; their bucket ranges tile the
timeline with no hidden gap or overlap. Nonce echo proves freshness; the
bounded counter rate and fixed per-bucket capacity cap total volume between
verifier checks, so history cannot be stretched, replayed, or run ahead of
wall clock.

**S6 — Unbiased estimates from sparse samples.** Because packets are
self-locating (S2) and per-packet information sums to total information,
uniform sampling over (bucket, byte) capacity is size-weighted sampling over
transmitted bytes: every byte is equally likely to be challenged, the prover
cannot steer which packet answers a challenge, and the sampled per-byte
unexplained-information estimate scales to a sound, unbiased bound on the
whole traffic — at a per-challenge cost of a few hundred hashes over ~100 KB.

## 4. Toward a verified interlock

The prototype was engineered for auditability, not yet verified. What makes
it a promising verification target is the shape of the TCB: a fixed-function
streaming pipeline of small RTL blocks, with properties that are naturally
trace invariants (S1, S2, S5 are statements about all executions of the RTL;
S3, S4 additionally assume standard crypto and the platform's key-storage
primitives). A verified version would, for instance, prove at RTL level that
every egress byte is either a committed packet byte released at a bucket
boundary or part of a prover-predictable frame; that the certificate stream
is a deterministic function of accepted wire bytes, the nonce, and the
counter; and that the counter logic never repeats an epoch across any
power-cycle sequence. The existing Python golden model, byte-exact
conformance suite, and independent-audit checklist provide the specification
and test infrastructure such an effort would build on.
