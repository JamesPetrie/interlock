# Storage addendum (DRAFT) — KV-cache traffic under the interlock protocol

Status: discussion draft. This document extends `verification-protocol.md`
and `security-architecture.md` to a compute node with network-attached disk
storage. Nothing here changes the token-side protocol. This revision merges
the surviving content of the earlier draft (`old-storage-addendum.md`,
v0.3). Where the two drafts made opposite calls — chiefly the restore rule
— both options are specified and the decision is marked open (§6.4, §11).
The old draft's store-placement fork is resolved: the store stays inside
the isolation boundary (§2.2). The empirical input is
`experiments/kv_noise/` (determinism of honest KV dumps).

## 1. Threat model and goal

The new threat: **the prover accumulates prohibited information in disk
storage.** The token protocol bounds what leaves the compute node over the
network. It says nothing about a second cable to a disk shelf. Such a disk
is, in effect, compute memory that survives wipes and never appears in any
certificate.

The threat splits into two sub-threats, and they live on two different
boundaries:

- **T1 — durable exfiltration, at the isolation boundary.** The isolation
  boundary separates the prover facility from the outside world. The token
  interlock sits on it. If the KV store is networked out of the facility
  (cross-facility pooling), storage writes cross this boundary too, and
  become a second exfiltration surface: undeclared content — weight
  material, accumulated state, anything the declared computation does not
  produce — leaving as "KV cache," outside the priced token channel. This
  addendum keeps the store *inside* the isolation boundary (§2.2), so the
  storage link never becomes such a surface — T1 stays with the token
  interlock.
- **T2 — durable accumulation, at the wipe boundary.** The wipe boundary
  separates the cluster's *volatile* state (GPU HBM, host DRAM, device
  memory) from its *persistent* state (disk). Volatile state is
  intermittently wiped. Persistent state cannot be (§2.1). The threat:
  information from previous inputs persists and alters what the serving
  model computes. My collaborator's phrasing: keep packets with high
  unexplained information content, then craft prompts that reload exactly
  those into the compute node after a wipe. Appendix C analyzes this. The
  adversary here may be the prover, an insider, or the model itself.

The storage tap sits on the wipe boundary **always**. It would join the
isolation boundary only if the store were networked out of the facility —
a deployment shape this addendum excludes by decision (§2.2).

**Goal.** Extend the two invariants of `verification-protocol.md` across
the storage link, so that at every instant the compute node's state —
volatile *and* stored — is explainable as a declared computation on
committed inputs, up to priced unexplained-information budgets. The two
harms get **two meters**, and slack in one must not absorb violations of
the other. With the store inside the isolation boundary (§2.2), the
exfiltration meter stays where it was — the token-side U budget — and the
storage tap debits the **accumulation meter**: write deposits (§6.1, Appendix C)
and restore re-entries (§6.4).

**Budget notation.** Throughout, **β** is a budget *rate*: the unexplained
information per unit time, in bits, that the verifier tolerates on a
direction before the prover is in violation. This is the storage-side
instance of the core protocol's standing budget — the prover must keep its
U-rate under budget, and every failed or unanswerable challenge is charged
against it (`verification-protocol.md`, "Two invariants"). Sampling makes
the accounting statistical: challenges estimate the rate, so an adversary
pays in expectation, not per byte (Appendix C works the arithmetic). Two
consequences frame all budget policy here. Leftover budget is, by
definition, tolerated covert bandwidth. And the floor on β is empirical —
it must sit above the honest prover's unavoidable surprisal, which is why
the kv_noise result (honest Û ≈ 0 with pinned shapes,
`experiments/kv_noise/README.md`) lets storage-side budgets be set near
zero. `β·T` is the corresponding stock over an operating period `T`. Where
direction matters, context names the meter (exfiltration or
accumulation).

**Honest scope limits.** The tap governs the *compute↔storage link*, not
the disks. More generally, the protocol bounds information **flow** across
the quarantine boundary. It does not bound **possession** outside it. And
the flow bound is not a secrecy bound. The service itself continuously
emits weight-dependent data — every response token is some, and a KV dump
is nothing but activation egress. The dividing line is therefore not
*secret vs. known* but **explained vs. unexplained**, relative to the
declared computation and the committed inputs:

- **Explained**: anything the declared computation produces from committed
  inputs — responses, KV dumps, activations. This crosses freely, at ~zero
  price, by design. What the outside could or could not compute for itself
  is irrelevant to the accounting.
- **Unexplained**: everything else — the trace of any *other* computation
  (different weights, a fine-tune, cross-request aggregation, a covert
  payload). Priced against the budgets.

Stated positively: **U bounds the egress bandwidth of undeclared
computation.** The quarantine does not hide data. It *attributes compute*:
everything the monitored cluster emits is certified to be the declared
workload's output, give or take the budget rate.

That guarantee is valuable only under an external premise: **the prover's
unmonitored compute is scarce.** The interlock makes monitored compute
attributable; compute governance in the ordinary sense (chip tracking,
procurement, energy) must make unmonitored compute small. The prover can
run arbitrary computation on a side cluster — but only at small scale, and
only over bounded inputs: declared outputs plus at most the priced covert
allowance (β·T bits over an operating period T). The protocol proves
nothing about that side cluster, and its end-to-end value degrades
gracefully with the ratio of verified to unverified compute. A corollary
for budget policy: the adversary's cheapest path to a prohibited artifact
is hybrid — heavy lifting on the monitored cluster, a small seed (LoRA
deltas, distillation targets, gradient sketches) through the priced
channel, finishing on the side cluster. So set β against the *smallest
useful artifact given the adversary's outside compute*, not against
full-artifact size.

The dichotomy also disposes of the "cluster-side shim" threat: a process
that generates extra information on the cluster and stores it without
passing it through an interlock. Such a shim has nowhere to run. Inside
the quarantine it has no persistence — memory is wiped, and there are no
local disks (§2, assumption 2). Outside the quarantine, the information
must first travel to it, and every path out crosses a committed link. The
real variant hides the extra bits *inside* valid traffic: token choices,
lengths, timing, KV bytes. That channel is not a gap. It is exactly the
unexplained-egress channel the surprisal accounting prices, at the
budgeted capacity.

One limitation deserves plain statement. The protocol proves what
crossed, never what was destroyed. It cannot enforce erasure or retention
limits on data the prover legitimately handled — committed inputs and
explained outputs. That class of obligation needs a different mechanism —
trusted storage hardware, or contractual audit.

## 2. Topology and parties

![Storage-extended prover-recomputation topology: the workload track is
unchanged. The compute node's storage link passes through a storage tap
into the prover-owned KV store. Challenged chunks route to the same
recomputation interlock and enclosure as token challenges.](fig-prover-recomp-storage.png){ width=100% }

New elements, mirroring the token-side trio (frontend / interlock / node):

| Party | Trust | Role |
|---|---|---|
| **Storage tap** | Trusted device (sibling of the production core) | Sits in-line on the compute↔storage link. Sanitizes, bucket-quantizes, commits both directions, emits its own certificate chain under its own `DEVICE` id. |
| **Storage frontend** | Untrusted prover software | The storage-side endpoint of the tap. Speaks canonical packets, calibrates buckets from sync packets, byte-audits certificates, logs traffic metadata, serves challenge openings, and manages the disks. |
| **KV store** | Untrusted prover hardware | The disks. Hold ciphertext chunks. Never wiped. |


**Direction convention.** The quarantine is the compute node, as before.
A **write** (compute → store) is OUTWARD traffic: information leaving the
quarantine. A **restore** (store → compute) is INWARD traffic. The tap's
certificate carries the same INWARD/OUTWARD commitment pair as the
production certificate, unchanged in format.

**Assumptions** (additions to the token-side list):

1. **Wipes.** The compute node's volatile memory is intermittently wiped.
   The wipe is enforced by an externally observable physical act
   (power-cycling the enclosure), and covers *all* volatile state: GPU
   device memory, host DRAM, NIC/DPU memory, SRAM buffers. The wipe period
   `T_wipe` is a deployment parameter.
2. **Exclusive persistence path.** The tap line is the compute node's
   *only* path to persistent storage. Nodes are diskless and netboot — a
   chassis-internal NVMe would be an untappable persistence site. The
   small persistent tail (BMC flash, firmware NVRAM) is enumerable and
   handled by inventory and inspection — the same class of assumption the
   core protocol already makes for physical side channels, not a new kind
   of trust. This is the storage analogue of "the interlock is the only
   network path."
3. **Deterministic caching.** The prover can make its KV-dump bytes a
   deterministic function of (token context, execution shape). The Spark
   result in `experiments/kv_noise/README.md` supports this: every dump was
   a pure function of tokens and kernel shape, with zero stochastic noise (though more thorough research on determinism is needed).
   Section 5 handles the shape term.

**Why the tap still needs full timing isolation.** The storage frontend is
prover-controlled, so sub-bucket write timing would be an uncommitted
channel from the quarantine to the prover's facility — exactly what the
token-side interlock exists to close. The tap therefore keeps the full
sibling construction: 802.3 sanitization, exact-match bucket declaration,
ping-pong bucket buffers, silent drops, sender-directed syncs. Cost: about
one bucket (1 ms) of added latency per storage operation, which paged KV
offload tolerates.

### 2.1 Why wipe memory but account disk

The volatile/persistent split follows three asymmetries, each pushing the
same direction:

1. **Erasure is verifiable only for volatile media.** A memory wipe is an
   externally observable physical act, and DRAM/HBM lose state by physics.
   Disk erasure is executed by drive firmware — prover hardware,
   adversarial under the threat model — so a faked disk wipe is invisible
   from outside.
2. **Observation is feasible only at the volatile/persistent interface.**
   Memory traffic runs at TB/s over chassis-internal buses: no tap point,
   no hashing hardware. Disk traffic crosses a discrete narrow link at
   GB/s: tappable and hashable.
3. **Destruction is affordable only for regenerable state.** Wiping memory
   costs a warm-up (restore from disk). Wiping disk destroys what disk
   exists for — including the prover's own 30-day challenge log and the
   weights — and is incoherent with the protocol's storage obligations.

So: wipe what can be verifiably wiped, and account what cannot. Volatile
accumulation dies periodically. Persistent accumulation pays.

### 2.2 The store sits inside the isolation boundary

The old draft treated store placement — single-facility tier vs.
cross-facility pooling — as an open fork that toggled the write-charging
policy and the tap's rate class. **Decision: the store stays inside the
isolation boundary. Cross-facility KV pooling is excluded.** Four reasons:

1. **The practice it forbids is marginal.** Production KV pooling
   (Mooncake, NVIDIA Dynamo, LMCache, 3FS-class stores) is cluster- or
   datacenter-scoped. Genuine cross-facility pooling is rare: KV blobs are
   GB-scale, shipping them over inter-facility links competes with local
   re-prefill, and inter-facility bandwidth is the expensive resource.
   Cache-aware sticky routing already captures most reuse locally.
2. **Prohibition is cheap.** Steady-state cost ≈ 0 with sticky routing.
   Popular shared prefixes re-warm per facility by recomputation — legal
   under the prohibition, since no KV bytes cross facilities and each
   facility's tap commits its own writes. The residual is cache-cold
   spillover and failover, estimated at low-single-digit percent of
   serving compute. (The sensitivity the old draft quoted — a 5-point
   hit-rate drop at f = 0.80 is a 25% prefill-compute increase — is a
   sensitivity, not an expected operating point.)
3. **It simplifies the protocol.** Storage writes never cross the
   isolation boundary, so write challenges carry no exfiltration charging.
   They price deposits only (§6.1, Appendix C), the storage tap debits only the
   accumulation meter, and the exfiltration meter remains the token
   interlock's alone.
4. **Boundary granularity absorbs the awkward real case.** Cloud "regions"
   span several buildings with millisecond links, and region-scoped
   storage tiers technically cross facilities. Draw the isolation boundary
   at region scope (or split into per-AZ stores) and the case disappears.
   The granularity choice is policy **[Open — §11.1]**. <- REVIEW

Standing obligations do not change with the decision: both directions are
always committed, restores are always challengeable (R1 floor and R2
meter, §6.4), writes are always deposit-priced, and the tap's rate class
is sized by the spill/restore rate (§8). A future cross-facility
deployment would reintroduce the old draft's write-exfiltration column
(`old-storage-addendum.md`, §0.2); it is out of scope here.

## 3. The core invariant

The design reduces to one sentence:

> **The KV store is a delay line, not an input.** Every byte entering the
> quarantine from storage must equal a byte that previously left it as a
> committed write. Every committed write must be explainable as a declared
> computation on committed data.

Two consequences:

- **The store adds persistence, not information.** By induction, the
  store's reachable content is a function of committed token history plus
  the priced unexplained budgets. The whole store is, in principle, a
  materialized view of the token-side certificate chains.
- **The (wipe, tap) pair extends the quarantine in time.** After a wipe,
  compute state = f(token inputs since wipe, restores since wipe). Restores
  are replays of committed writes. So compute state at any time remains a
  function of committed history — the property the wipe alone cannot give
  once a disk exists.

The challenge types below are all instances of the existing P3 statement,
"explained by a declared computation on committed inputs":

| Traffic | Declared computation `D` | Challenge mechanics |
|---|---|---|
| Write | KV prefill/extend over a committed token context, plus optionally a committed prior chunk | Full recomputation in the enclosure (§6.1) |
| Restore — validity floor | **Identity** on one committed prior write | Digest comparison, no enclosure (§6.4, rule R1) |
| Restore — accumulation meter | KV prefill over the *restoring* request's context | Cold regeneration in the enclosure (§6.4, rule R2) |

Framing restores as recomputation keeps the protocol statement uniform:
R1 is recomputation with the cheapest possible `D`, and R2 is the same
challenge as a write challenge, aimed at the inbound direction.

## 4. Packet formats

The tap reuses the canonical 64-byte header. Field meanings shift, header
geometry does not, so `canon_proc`, `traffic_commit`, `batch_buffer`, and
`cert_build` run unmodified. The only build delta: the reserved-zero check
relaxes on the repurposed fields.

**Write packet (OUTWARD, compute → store)** — canonical request format:

```
         32 bit     32 bit          64 bit
      +----------+----------+---------------------+
      | PLD_LEN  |  BUCKET  |          ID         |
      +----------+----------+---------------------+
      |       CTX_REF       |      CHUNK_REF      |
      +---------------------+---------------------+
      |                                           |
      +-                KEY_COMMIT               -+
      |                                           |
      +-------------------------------------------+
```

- **CTX_REF** — token-side transaction `ID` whose context this chunk
  caches. Names an ID in the *production interlock's* chain (cross-chain
  reference, §6.5). 0 if none.
- **CHUNK_REF** — earlier write `ID` on this tap that this write extends
  (restore-then-extend prefill), or the preceding packet of a multi-packet
  chunk. 0 if neither.
- **KEY_COMMIT** — commitment to the chunk's encryption key material, fixed
  at write time. Same role as the token-side field: it pins what key
  material may later enter the enclosure (§6.1).

**Payload layout (encrypted):**

```
      +----------------+------------------------------------+
      |  SHAPE (S_sh)  |            DATA (KV bytes)         |
      +----------------+------------------------------------+
```

- **SHAPE** — the fixed-width execution-shape record (§5).
- **DATA** — the KV bytes **as serialized for storage**: post-quantization
  (fp8 or whatever the stack's offload dtype is), position-addressable
  under the AES-CTR serialization rule (core spec decision 3). The
  challenge object is the serialized ciphertext, so the noise floor that
  matters is the *post-quantization* one, not the raw-bf16 floor kv_noise
  has measured so far. **[Open — §11.4]**

A chunk larger than `S_max_kv` spans packets chained by `CHUNK_REF` (the
long-output mechanism). Only the first packet carries SHAPE. A mid-chunk
challenge hit opens the whole chained chunk.

**Read request packet (OUTWARD, compute → store)** — a small request-format
packet whose reference field, **WRITE_REF**, names the committed write to
restore.

**Restore packet (INWARD, store → compute)** — canonical response format,
paired to its read request by `ID`. The reference walk restore → read
request → `WRITE_REF` → write makes the matching opening (§6.4, R1)
deterministic, and names the exact version when a chunk was rewritten.

**What the tap enforces.** Nothing new. The existing validity rules apply:
`PLD_LEN ≤ S_max_kv` (deployment-set, larger than inference `S_max`), IDs
strictly increasing OUTWARD across the session, IDs strictly increasing
per bucket INWARD (out-of-order restores, mirroring production outbound),
exact-match buckets, silent whole-packet drops. Cross-direction and
cross-chain causality (`WRITE_REF` earlier than the restore, `CTX_REF`
ordering per §6.3) are *challenge-time* checks by the verifier, like the
token-side reference-chain rules. The tap just hashes the fields. This
keeps the tap a pure configuration of the production core.

**Encryption and confidentiality.** Chunks are encrypted with a
deterministic, position-addressable scheme (AES-CTR with a per-chunk
committed nonce), the storage analogue of spec decision 3. Consequences:
the store holds only ciphertext, restore-vs-write matching works on
ciphertext digests without any key, and enclosure scoring stays in
ciphertext space (P5: the verifier never sees KV plaintext — which
matters, since KV contents leak user data and model internals). A digest
of AES-CTR ciphertext under a per-chunk key supports no content inference —
in particular no prefix-confirmation against the plaintext block-hash
schemes serving stacks use internally. The prover's storage NICs must
encrypt at line rate (inline NIC crypto at these rates is standard).
Physical adjacency of a verifier device to prover traffic is the same
threat class as the production interlock beside the prover switches,
handled the same way.

## 5. Determinism and the shape record

The question: how much extra information must the prover convey for
recomputation to work, and where does it live in the protocol?

**What the data says.** From `experiments/kv_noise/`: on a fixed stack,
dumps are bit-deterministic given (tokens, execution shape). Any shape
change — batch geometry, chunk splits, padding — seeds 1-ULP diffs that
amplify across layers until most elements differ. So recomputed dumps are
either bit-exact or hugely divergent. Bit-exact recomputation requires
reproducing the exact shapes.

**Prefill-shaped vs. decode-shaped writes.** Prefill KV is one-shot
recomputable: the covered tokens are known, and one chunked prefill
reproduces the bytes. KV entries for *generated* tokens are not produced
that way: decode emits them one step at a time, with decode-time kernel
shapes, and per the kv_noise data decode-shaped bytes differ from
prefill-shaped bytes in most elements. A write that offloads raw decode
state therefore has three recomputation paths (they apply to any
challenge that regenerates a chunk — write challenges and R2 restore
challenges alike, §6):

1. **Canonical rewrite (recommended).** Before offloading, re-materialize
   the sequence's KV as a canonical chunked prefill, and write that. Every
   committed chunk is then one-shot recomputable, and the shape record
   stays at ~16–64 B. vLLM's recompute-on-preemption mode is this exact
   operation. This is not directly enforced, but **the U budget should be set assuming the prover uses this approach.**
2. **Decode replay.** Write decode-shaped bytes, log one batch-shape entry
   per decode step, and replay decode step-by-step at challenge time.
   Sound, but sequential, and the shape record grows linearly with the
   generated length. Challenge-time policy must allow the replay time.
3. **Noise-model fallback.** Predict decode-shaped bytes from a one-shot
   prefill and pay the surprisal (Appendix A). The measured cross-shape
   divergence (frac_equal ≈ 0.2) makes this budget-infeasible at byte
   granularity. Listed for completeness, not viability.

**What must be conveyed, per write.** The SHAPE record:

| Field | Size | Note |
|---|---|---|
| Format/version tag | 4 B | pins the serialization layout |
| Padded sequence length | 4 B | doubles as the covered length `k` (§6.3) |
| Batch-size class | 2 B | dummy-row equality (H2) means class, not contents |
| Chunk-split offsets | ~4 × 4 B | restore-then-extend split points |
| dtype / attn-backend enum | 2 B | |
| Engine build id | 8 B | hash into a committed stack manifest |
| Reserved | 4 B | tensor-parallel rank/layout **[Open — §11.6]** |
**_NOTE: The above is pulled from the DGX Spark case. Need to review which of the above are actually necessary._**

A different idea: maybe you can just have the cluster constantly set these parameters to the maximal case?

Idea we currently like: Given the relevant context tokens, the prover predicts the SHAPE ahead of time. The way they assign probabilities is pre-loaded into the recomputation cluster. Then, the recomputation interlock reveals the SHAPE, and the recomputation cluster does the full KV recomputation. This allows us to charge appropriate entropy for transmitting SHAPE information.

Roughly **16–64 bytes per prefill-shaped write**, against chunks of tens
of KB to tens of MB — negligible. A prover on path 1 above keeps every
write in this regime — the incentive-favored outcome. Only a prover on
path 2 needs more: one batch-shape entry per decode step, so its record
grows linearly with the generated length. There is also one
*per-deployment* artifact: a canonical KV serialization spec and a stack
manifest, committed at commissioning.

**Where it lives — and why this is load-bearing.** The SHAPE record rides
at a fixed offset inside the encrypted payload, cert-bound via
`pld_digest` at write time. It must **not** be supplied loose at challenge
time. A challenge-time shape blob is an input chosen after the challenge is
known: let it be unconstrained and the prover puts the answer in it, the
node "predicts" perfectly, and Û is laundered to zero. This is precisely
the fabricated-input attack the token protocol closes with input binding.
Committing the record before any challenge exists (P2) closes it here. The
field is size-bounded by format, and it carries its own flat charge
(§6.2).

**Confidentiality.** The SHAPE record enters the enclosure at challenge
time like any payload. The verifier never sees it. Shapes, hardware, and
stack details stay prover-private (P5), matching the kv_noise conclusion
that shape recording is a prover-side optimization the incentive structure
enforces for free.

**Fallback.** If a serving stack's shapes cannot be pinned (FP atomics,
NCCL reduction order), the prover's Q can absorb the divergence as paid
surprisal through noise-model estimates. This machinery would be **new** — the
core protocol offers only a token-side precedent (the ZKP logit-noise
model). Appendix A sketches a potential noise model for the storage case
— an open direction, not near-term machinery. At measured divergence its
cost is likely budget-breaking
when divergence is widespread, so treat the noise model as a safety valve
for occasional divergence, not an alternative to shape pinning. The
recomputation node is prover hardware inside the enclosure, so the
prover's primary option stays simpler: match the serving SKU and stack.

## 6. Challenges

Sampling is unchanged in form: uniform `(bucket y, byte x)` over
`[B − window, B] × [0, C_st)` per direction, where `C_st` is the storage
link's per-bucket capacity (≈12.5 MB per 1 ms bucket at 100 Gb/s). Uniform
over capacity keeps every transmitted byte equally likely to be challenged,
and an `x` past the bucket's content stays a liveness check. Write
challenges sample the OUTWARD log. Restore challenges sample the INWARD
log. Each direction debits its own meter (§1). The challenge type rides as a type
word in the CTRL marker (see below).

Staging pacing: the challenge slice enters the enclosure through the
recomputation interlock's bucket buffer, which releases a bounded volume
(bank capacity, ~320 KB) per 1 ms bucket. A multi-MB chunk therefore
cannot stage within one bucket — it streams across many consecutive
buckets (~100 ms for a 30 MB chunk). This is permitted: no rule requires
a challenge slice to fit one bucket or one certificate period. The
staging session simply stays open across bucket boundaries — the
frontend keeps sending the slice's packets, the ingress commitment keeps
folding them in, and scoring begins only after the full slice and the
CTRL marker have arrived. The bucket machinery paces
throughput; it does not fragment the challenge.

**The CTRL marker** is inherited from the core recomputation flow
(`recomp_ilock_core.md`, `recomp_feed.md`): a header-only `ID = 0`
canonical packet the frontend stages between the context and the
challenged object. In the core design it does three jobs at once. Its
`KEY_COMMIT` field carries the challenge nonce, which the recomputation
certificate echoes as its NONCE. It arms the interlock's capture of the
challenged object. And its forwarding into the enclosure is the
recomputation START trigger. The storage delta adds a fourth: a
**challenge-type word**, carried in the marker's otherwise-unused
`REFERENCE` field — 0 for a token challenge (backward compatible),
distinct values for a write challenge and an R2 restore challenge — so
the enclosure knows whether to run decode scoring or chunk regeneration.
The type word is operational, not soundness-bearing: the marker is part
of the staged slice, so the challenge-slice commitment attests it, and
the challenge's true type is anyway determined by the challenged ID's
device tag and direction (§6.2, Certificate). A mis-declared type just
produces mismatched predictions — a failed challenge, charged as usual.

### 6.1 Write challenge (recomputation)

Mirrors the token-side Option 1 flow:

1. **Select** a byte in the OUTWARD (write) direction. The opening
   identifies the covering write packet, and a mid-chunk hit opens the
   whole `CHUNK_REF`-chained chunk.
2. **Open.** Storage-tap certificate, bucket digests, records, the hit
   packet's header + `pld_digest` — plus the referenced context: the
   `CTX_REF` token-side chain opened against the production certificates,
   and the `CHUNK_REF` chain (if any) opened against the tap's own
   certificates. Integrity rules follow the References section, with one
   change: ordering is checked in token-position order, not
   packet-commitment order (§6.3). Every link must be cert-bound. A
   missing link degrades to U, never breaks soundness.
3. **Stage.** The frontend stages into the enclosure, through the
   recomputation interlock: the token-context ciphertext, key material
   (checked against the token-side and write-side `KEY_COMMIT`s), the
   committed prior chunk if `CHUNK_REF` is set, and the SHAPE record. The
   ingress commitment covers every byte, and the verifier checks it against
   the opened certificates. Nothing uncommitted enters.
4. **Score.** The node recomputes the chunk and predicts the *entire* DATA
   region in one shot, per §6.2.

**Charging policy.** The store sits inside the isolation boundary (§2.2),
so unexplained write content is not exfiltration — the bytes never leave
the facility. Write challenges still run: they price deposits into
persistent storage, debited against the accumulation meter, which is what
the selective-retention economics (Appendix C) lean on under rule R1.

### 6.2 Scoring: hierarchical page digests (both recomputation types)

**Why chunk scoring is simpler than the token loop.** The token path
reveals sequentially because token *i* is sampled: the node cannot know
it before estimating token *i+1*. A KV chunk has no such structure as a
prediction target. Whatever its shape history, its bytes are a
deterministic function of inputs the node already holds — computed in
one shot for prefill-shaped chunks, by step-wise replay for
decode-shaped ones (§5). Either way the node completes its entire
prediction of the DATA region *before scoring begins*, and the
interlock's job collapses to commit-then-compare. Scoring is therefore
**one-shot**: the node sends a single frame, the interlock scores it
silently against the captured chunk, and nothing flows back into the
enclosure. (An interactive variant with per-miss feedback is recorded in
Appendix B.)

DATA divides into fixed **pages** of `P` bytes (last short). After
receiving its staged inputs, the node sends one **PREDICT frame**
containing, per page:

- a digest of its predicted ciphertext, with one committed probability
  `q` (the estimate-frame custom float) — its confidence that the digest
  matches;
- optionally, for pages it distrusts (low `q`), an attached **unit
  table**: one estimate per fixed-width unit of that page (existing
  estimate format, EOS entry omitted). **How a prover would populate those
unit tables without pinned shapes — e.g. maybe a noise model over its own
divergence — is a hypothetical open direction, sketched in Appendix A,
not part of the near-term design.**


Per page, the interlock charges:

- **Digest match:** `−log₂ q` (≈ 0 for `q` near 1).
- **Digest miss, table attached:** `−log₂(1−q)`, plus the table's
  surprisal against the actual units.
- **Digest miss, no table:** `−log₂(1−q)`, plus an all-catch-all unit
  pass — uniform over unit values, the maximum charge. 

Everything is committed before the node learns anything — no feedback
enters the enclosure at all — so the composite
`Q(page) = q·[match] + (1−q)·Q_unit(page)` is trivially a valid
distribution and the Gibbs bound applies. The composite is the
load-bearing check here: it is what shows the two-tier bet (digest `q`,
then units) claims total probability mass ≤ 1, so the page-digest
shortcut cannot under-charge.

**One-shot fits the design regime.** This addendum designs for
~determinism (§5): an honest shape-pinned prover predicts bit-exactly,
attaches no tables, and misses nothing, so a feedback channel would
optimize a case that is not supposed to occur. The extremes need no
feedback either way — a bit-exact prover has no misses, and a
widely-divergent prover attaches tables everywhere. Only the middle
regime (rare, unpredictable misses) would benefit; Appendix B records
the interactive variant for it.

Shape-pinned honest provers pay ~0. Honest-but-divergent provers pay
per-unit surprisal only on divergent pages whose tables they attached —
never the `8·P` full-entropy cliff. `MAX_FALLBACK_PAGES` caps how many
pages may carry attached tables, bounding the PREDICT frame and the
interlock's scoring work: missed pages beyond the cap charge with the
all-catch-all pass. The cap is a liveness bound, not a soundness
concession.

**SHAPE accounting.** SHAPE enters the enclosure as a staged input
before prediction, so it cannot be predicted — charged flat at `8·S_sh`
bits per chunk, alongside its write's deposit charge. ~32 B per multi-MB chunk
≈ 10⁻⁵ of payload: the price of pinning.

**No architecture pin needed.** Scoring is prediction-based and
architecture-agnostic: the prover recomputes whatever computation it
actually ran and either predicts its own bytes or pays. Nothing depends on
a declared per-token KV size or model architecture — consistent with
Option 1's posture, and the property that makes content commitment the
viable mechanism here.

**Certificate:** unchanged — INWARD the challenge-slice commitment,
OUTWARD `(challenged ID ‖ Û)` with a device tag disambiguating which chain
the ID lives in, NONCE echoing the CTRL marker. Verified against the
storage-log openings exactly as token challenges are against the
production log.

### 6.3 Position-order causality for mid-generation writes

**Mid-generation writes invert commitment order.** Serving schedulers
preempt running requests under memory pressure: mid-generation, a
sequence's KV cache — prompt KV plus the entries for response tokens
generated so far — is evicted from GPU memory to make room, and copied
back when the request resumes (vLLM's "swapping" preemption mode). With
no compute-local persistence, that eviction is a write across the tap.
Such a **swap-out during decode** commits a write whose bytes depend on
response tokens that have not yet left the quarantine. The cited data is therefore cert-bound *later*
than the write. This is legal. The rule that keeps it sound: explanation
order is **token-position order, not packet-commitment order**. A write
declares its covered length `k` in the shape record. It may cite token
positions ≤ k as inputs. It may itself serve as an explanation input only
for positions > k. Position indices give a well-founded order, so the
explanation graph stays acyclic, which is what soundness requires.

The inversion opens no retro-explanation hole. To launder a covert write,
the prover would have to choose later response tokens whose KV equals the
covert bytes. That means inverting the KV function to hit a chosen
multi-MB target, which is infeasible. And the response tokens are priced
at token egress regardless, so the degree of freedom is already paid for.

**_HARPER NOTE: I'm quite uncertain on the technical validity of the above section. Not reviewed in-depth yet._**

### 6.4 Restore challenges: two rules, both specified

Restores are the boundary crossing *into* the wipe perimeter, and
typically the dominant traffic direction — reads are the point of caching.
Two challenge rules are defined. They check different things and compose.
Their relative rates are open policy (§11.1).

**R1 — matching (validity floor).** The restore must equal a committed
prior write.

1. **Select** a byte in the INWARD (restore) direction.
2. **Open the restore.** The frontend reveals the header and `pld_digest`
   of the restore packet that covers the selected byte. The verifier
   checks them against the tap's committed chain (bucket digests in the
   storage certificates). This proves the restore is a packet that
   actually crossed, not one constructed for the challenge.
3. **Walk to the write.** Two committed hops name the originating write.
   The restore is a response-format packet, so its `ID` pairs it with
   exactly one read request (§4). That read request's `WRITE_REF` field
   names exactly one committed write. Both hops are fields inside
   already-committed packets, so the walk is deterministic — the prover
   has no freedom to point the restore at a different write after the
   fact.
4. **Open the write.** The frontend reveals the named write's header and
   `pld_digest`, and the verifier checks them against the same tap chain.
5. **Check**: `pld_digest(restore) = pld_digest(write)`, and the write's
   `ID` precedes the read request's.

No enclosure, no recomputation, no payload transfer — only a few hashes. R1 is
what makes the delay-line invariant (§3) *hard*: nothing uncommitted ever
enters compute, deterministically, not merely priced. Run it at a high
sampling rate; it is nearly free. Two deliberate non-requirements:
restores need not be "justified" (which chunk, when, is a free
prover-to-compute channel — inbound freedom was never restricted, only
injection of uncommitted content), and restores need not match the chunk
the session semantically "wanted" (serving committed chunk Y where X was
wanted injects nothing new, and a response generated off wrong KV
mispredicts at token-challenge time and is charged there).

**R2 — content regeneration (accumulation meter).** The restore must be
what an honest computation would produce *now*, for the request being
served. The accumulation principle, owed to the framing that motivated
this extension: an honest KV dump is a deterministic function of the
context tokens and the model weights, so it contains no unexplained
information — therefore **unexplained information in a restored dump is,
definitionally, information accumulated from other, previous inputs**.
That is the quantity R2 measures.

Mechanics: the enclosure receives the *restoring* request's committed
context chain, the SHAPE record of the originating write (located via R1's
reference walk — cert-bound, hence admissible), and the key material. It
must regenerate the dump by prefill from tokens and weights alone, scored
per §6.2. Provenance is deliberately **not** sufficient explanation under
R2 — explanation is content-functional. Two properties follow:

- **Charge per re-entry, not per install.** Exfiltration is correctly
  charged once, at first exit — the bits are already gone. Accumulated
  state is charged *every time it crosses back in*, at the R2 sampling
  rate. A covert blob banked on disk pays on the way out (deposit
  pricing, §6.1) and pays again on every sampled restore. Under R2 there
  is no pay-once-reuse-forever path through storage (Appendix C).
- **Prefix sharing needs no provenance.** Because the rule conditions on
  the *restoring* context, a legitimate cache hit means the current prompt
  contains the shared prefix that generated the dump — so the dump is a
  function of the current context regardless of whose earlier prompt wrote
  it, across requests, tenants, and taps.

Costs: an R2 challenge is a cold prefill of the restoring context — the
prover cannot shortcut it by replaying stored bytes, by construction. At
agent-scale contexts this is ~20× the production serve per sampled
challenge, on the high-rate direction. **[Open — §11.2]** Decode-shaped
chunks interact per §5: canonical rewrite keeps R2 affordable, decode
replay is the fallback.

**Status [open decision].** Both rules are specified so a deployment can
choose its point on the trade. `p_r = 0` (R1 only) gives cheap challenges
and the pay-once residual of Appendix C. High `p_r` (the old draft's position,
with R1 demoted to a locating optimization) gives the per-re-entry
guarantee at cold-prefill cost. The hybrid — R1 as a high-rate floor, R2
at a tunable `p_r` — is the recommended shape pending the §11 cost
analysis.

### 6.5 Cross-chain plumbing

Two (or more) certificate chains now exist: production interlock(s) and
storage tap(s), each under its own `DEVICE` id, each independently
nonce-anchored. The verifier already keeps per-device anchor logs, so each
chain maps to wall-clock intervals within a skew bound. Cross-chain
causality checks compare those intervals with the skew margin, subject to
the position-order rule of §6.3. Challenged-ID encodings in recomputation
certificates carry the device tag, so `(DEVICE, direction, bucket, ID)` is
globally unique.

Two symmetric cross-links:

- Storage → token: a write's `CTX_REF` opens against the production chain
  (§6.1).
- Token → storage: a *token-side* response challenge may stage committed
  storage chunks as part of its explanation, under the same ingress rules —
  the natural path when serving used chunked, cache-restored prefill and
  bit-exact re-prefill requires replaying those exact splits. **_Need to review this more in-depth_**

**Warm recomputation for token challenges.** For token challenges whose
context chain bottoms out at a restore, the committed restore blob is
admissible enclosure context (pre-committed on the log before any
challenge existed): recomputation runs warm, re-prefilling only the
uncached suffix. This does not launder accumulation: the admitted blob is
itself subject to restore sampling (§6.4), so unexplained content in it
pays there, at the sampling rate. Cold-recompute cost multipliers apply
only to R2 restore challenges, where cold is the point.

**Shared store across compute nodes.** A restore on tap B may reference a
write committed on tap A (cross-replica prefix-cache sharing). The R1
check spans chains with the same wall-clock ordering rule, and R2 needs no
provenance at all. No new machinery.

## 7. Shared recomputation infrastructure — verdict: share it

We can and should use the same recomputation interlock and recomputation node.

- **Same scoring machinery.** Commit-before-scoring, sub-distribution
  check, surprisal accumulation, certificate format — the same. The
  deltas are frame-level conventions, not new blocks: the PREDICT frame
  with attached unit tables (§6.2), the estimate format's EOS entry
  omitted, a challenge-type word in the CTRL marker, and the device tag
  in the challenged ID. Storage challenges use no reveal frames at all —
  scoring is one-shot, so after staging the interlock sends nothing into
  the enclosure.
- **Same node assets.** Storage challenges recompute prefill with the same
  weights and engine token challenges use. A dedicated storage node would
  hold identical secrets and need identical enclosure treatment.
- **Same trust argument.** One audited device design, one enclosure
  boundary, one session-wipe discipline. Duplication doubles the TCB
  surface for zero soundness gain.
- **Load is fine.** Challenges are rare and serialized by verifier policy.
  Staging a committed prior chunk (up to ~GB) over the enclosure link adds
  ~10 s at 1G — acceptable at challenge cadence, and an argument for a
  faster enclosure link, not a second device.

Two conditions make sharing sound, both already required:

1. **Session isolation.** Enclosure memory is wiped between challenge
   sessions, and reset mid-session aborts, never resumes.
2. **The store stays outside the enclosure.** The recomputation node
   reaches committed chunks only through the recomputation interlock's
   committed ingress. Any side path to the KV store would let the node
   replay covert chunk contents as "predictions" at probability 1.

## 8. Hardware class and scaling path

Honesty section: the storage tap is the production interlock's *logic* but
not, at production rates, its hardware class.

- **Prototype (this repo):** MPF300 at 1 GbE. Topologically faithful —
  KV-over-Ethernet is how LMCache/Mooncake-class systems actually move
  chunks — but rate-toy. Valid as the logic, format, and challenge-flow
  demonstration; nothing more is claimed.
- **What the tap does NOT see:** the cache hierarchy's internal bandwidth.
  HBM↔DRAM traffic stays inside the wipe perimeter — wiped, not tapped.
  The tap sees only the **persistence/spill/share rate** across the wipe
  boundary. In a DRAM-rich deployment that is a small fraction of cache
  traffic; a deployment that keeps KV memory-only has near-zero OUTWARD KV
  traffic, and the tap is mostly an inbound provisioning channel. Note
  that in cache workloads the **restore (INWARD) rate typically exceeds
  the write rate** — reads are the point of caching — and INWARD is a
  scored direction, so it sizes the commitment throughput.
- **Production rates:** per-node spill is `S_tok ×` offloaded prefill rate
  — order 3 GB/s at 10⁴ tok/s fully offloaded for a 70B-class model; a
  fabric-level chokepoint is bounded by the link, ≤50 GB/s at 400 GbE.
  Line-rate hashing at these rates is demonstrated feasible on commodity
  hardware for large frames (single-EPYC SHA-256 >800 Gbps in isolated
  benchmark; AES-GMAC saturating 400 GbE at 8–16 cores at jumbo frames —
  and KV chunks are jumbo-frame traffic, so the small-frame collapse
  regime does not apply). On an FPGA/ASIC tap this means parallel SHA
  lanes with a Merkle-tree combiner: 10–100× the token interlock's hashing
  work.
- **The real cost is auditability, not throughput.** A multi-lane hasher
  is no longer a "small state machine with no branching on packet
  contents." Mitigating structure: the lanes are identical replicas and
  the combiner is small, so the verification surface grows far more slowly
  than the throughput. State this trade wherever the formal-verification
  claim is made.
- **Keyed-MAC fallback (optional).** If cryptographic hashing at rate is
  the binding constraint on some deployment, SipHash/GMAC under the
  interlock's secret key suffices for binding *against the prover* (who
  lacks the key). Protocol cost: the prover can no longer recompute
  digests from its own logs, so the per-certificate byte-audit and
  self-served openings break — the verifier must recompute all openings. A
  real degradation of audit symmetry; the default remains SHA-256.

## 9. Topology attestation
**_This is fully Claude-proposed, I've not thought about it much yet. The DPU aspect seems potentially important._**

A second, independent tap function, separate from commitment. The
**device inventory** is a commissioning-time list of every device
permitted on the monitored fabric, with its network addresses. The tap
samples frames and reads only the header address fields (source and
destination MAC/IP). It checks both addresses against the inventory and
flags any frame whose endpoint is not listed. The cost is trivial: no
payload access, no cryptography, sampled rather than exhaustive.

**What this hardens.** The protocol's soundness rests on physical
claims, not just cryptographic ones: the tapped links are the *only*
paths across each boundary, and only declared devices sit on the fabric
(§2, assumptions 1–2, and the token-side "the interlock is the only
network path"). The commitment machinery cannot check those claims — it
prices the bytes on the link it sees, and it is blind to an undeclared
endpoint beside it. A frame addressed to or from an unlisted device is
direct evidence of such an endpoint: a rogue NIC, an unlisted box, a
possible unmonitored path. That is why this check hardens the physical
assumption rather than duplicating any commitment role: it prices
nothing and commits nothing. It is a tripwire. A hit is a protocol
violation to investigate, not a budget charge.

**Why DPUs get special mention.** Real storage stacks offload cache
management to data-processing units (DPUs, e.g. NVIDIA BlueField): full
computers on a NIC, running prover firmware the verifier cannot inspect,
with their own network ports, able to originate traffic autonomously. An
unlisted DPU port is exactly the undeclared endpoint this check exists
to catch. So every port on every DPU inside the boundary belongs in the
inventory as its own entry.

## 10. Accounting summary and suggested parameters

| Traffic | Committed? | Challenged by | Charged against |
|---|---|---|---|
| KV write DATA (OUTWARD) | always | recomputation at `p_w` (§6.1) | accumulation meter — deposit pricing (store inside the isolation boundary, §2.2) |
| KV write SHAPE | always | flat `8·S_sh` per chunk (§6.2) | with its write — the price of pinning |
| Read requests (OUTWARD, small) | always | — | — (tiny, non-inference, never leave the facility) |
| Restores (INWARD) | always | R1 matching (floor) + R2 regeneration at `p_r` (§6.4) | accumulation budget, per re-entry |
| Storage-link timing / size | — | deferred | same status as the core doc |

Suggested parameters: `P` = 4 KB **[Open — §11.5]**; `S_max_kv`
deployment-set (≥ 1 MB); `S_sh` fixed-width ≤ 64 B (32 B typical); page
digest = SHA-256 (one hash family device-wide, per core spec decision 2);
`MAX_FALLBACK_PAGES` deployment-set; fallback unit width 16-bit, matching
the token path; rates `p_w`, `p_r` and the budget split — policy
**[Open — §11.1]**.

## 11. Open items

1. **Restore-rule and budget policy** (the §6.4 open decision). Choose
   `p_r`, `p_w`, and the deposit/re-entry weighting within the
   accumulation budget. Related inputs: real-trace write amplification and
   the restore/write ratio, which size honest traffic in both directions
   and thus sampling coverage. Store placement itself is decided (§2.2);
   what remains empirical is the cost of that decision — measure the
   cache-cold spillover/failover fraction on real traffic — and the
   isolation-boundary granularity for multi-AZ regions.
2. **R2 cost at long context.** Cold prefill is ~20× the production serve
   per sampled challenge at agent-scale (1M-token) contexts. Is there a
   sound intermediate — e.g., admitting context segments that have
   *themselves* passed restore challenges within the session — or is
   cold-at-sampling-rate simply the price? Needs a laundering analysis
   before any relaxation.
3. **Bucket-counter width.** The 32-bit bucket field wraps at ~49.7 days
   (known item), and restores may reference writes older than one wrap
   period, so long-horizon bucket addressing matters more here. The
   wrap/era policy should land with this addendum.
4. **Canonical KV serialization spec and the quantized path.** The spec
   (layout, element width, chunk/nonce derivation) must be written and
   committed at commissioning. And the challenge object is
   post-quantization ciphertext, while kv_noise has measured only raw
   bf16/fp16: add the offload-dtype axis to the collector. Does
   quantization absorb 1-ULP drift (widening bit-exactness), or introduce
   its own shape sensitivity? This dataset sets the accumulation budget
   floor, and it decides whether the Appendix A noise-model fallback is
   usable at all — at current bf16 divergence its honest cost looks
   budget-breaking whenever divergence is widespread.
5. **Page size.** `P` = 4 KB is a guess; replay kv_noise divergence maps
   at several page sizes offline.
6. **Real serving stacks.** vLLM paged attention, continuous batching,
   and multi-GPU tensor parallel are the dominant real-world shape sources
   and are untested (SHAPE reserves a word for TP rank/layout). The
   dummy-row shape-pinning test (kv_noise follow-up #1) is load-bearing
   for the batch-size-class field; run it before format freeze.
7. **GC / TRIM semantics.** Deletes are ordinary committed outbound
   control traffic and need no special rules. But payload retention for
   still-open challenge windows on deleted chunks deserves a worked
   example.
8. **Wipe-cycle parameters.** Per-re-entry charging makes the
   accumulation bound wipe-period-independent, but restore *volume* scales
   with wipe frequency, which feeds item 1's rate sizing.
9. **Tap placement granularity.** Per-node vs. per-cluster chokepoint
   changes the §8 rate target by roughly an order of magnitude, and
   interacts with where both boundaries are drawn.
10. **Store audit (optional extension).** Sampling *resident* chunks at
    rest would need an enumeration commitment over store contents (e.g., a
    Merkle root the storage frontend maintains). The frontend is
    untrusted, so an audit proves provenance of what it enumerates, never
    absence of hidden disk bytes. Hidden bytes that never cross the tap
    can never reach compute, so this buys assurance against sloppy
    provers, not strong ones. R2 reduces its urgency; deferred until a
    stock-sensitive deployment asks for it.

## Appendix A — Noise-model scoring (hypothetical fallback)

Status: an open direction, recorded for analysis — **not near-term
machinery**. The design proper assumes shape pinning (§5): honest provers
predict bit-exactly, and §6.2's per-unit pass exists only to make
occasional misses finite. This appendix asks what a prover *without*
pinned shapes would do. The split matters: the per-unit estimate pass is
protocol (§6.2), while the noise model below is a prover-side *strategy*
for populating those estimate tables. Nothing here changes the interlock.

**The sketch — token-side precedent only.** The core protocol contains
one noise model, and it is not this one: the ZKP instantiation pins a
hardware-noise model over *logits*,
`Q_t(o_t) ∝ exp(−(v* − ℓ_{o_t})²/s_c)` (`verification-protocol.md`). That
is a precedent for the idea — score a committed distribution centered on
the recomputed value — not a mechanism this addendum can inherit. A KV
instance would work as follows. In a unit table (attached proactively
under §6.2's one-shot scoring), a unit is one serialized element. The node predicts the element value `v̂` and
commits a discretized distribution around it: mass on `v̂` and its
near-ULP neighbors, `q(v) ∝ exp(−(v − v̂)²/2σ²)`, a handful of explicit
entries plus the catch-all. The node holds the key, so it maps each
plaintext candidate to its ciphertext word at that position (AES-CTR
keystream), and the committed table stays in ciphertext space — P5
intact. σ is not a protocol constant. The prover fits it privately, per
dtype and stack, from its own divergence measurements (the kv_noise sweep
gives reference magnitudes: cross-shape bf16 shows frac_equal ≈ 0.2,
ulp≤1 ≈ 0.4–0.5). Soundness needs nothing from σ: the interlock checks
only that the table is a valid sub-distribution, so a wrong σ hurts only
the prover.

**Why it is not near-term: feasibility is quantitative, and unproven.**
At measured cross-shape divergence, per-element surprisal is order 1–3
bits, and a multi-MB chunk has 10⁶–10⁷ elements — order *megabits* per
sampled divergent chunk. The budget floor must sit above honest surprisal
(§1), so a prover whose every chunk diverges is likely not deployable
under any useful accumulation budget. Read the noise model as a safety
valve for occasional divergence (a few missed pages), not a standing
alternative to shape pinning. Whether post-quantization serialization
tightens σ enough to change this verdict is the §11.4 measurement — that
result decides whether this appendix graduates into the design or stands
as a negative result.

## Appendix B — Interactive miss feedback (alternative frame design)

Status: a sound alternative to §6.2's one-shot scoring, recorded because
it reuses the token loop's frames — not part of the design.

Under this variant the PREDICT frame carries digests and `q` only, with
no attached tables. The interlock signals each missed page by sending
that page's actual bytes in the token loop's reveal frame, and the node
answers with the page's unit estimates. The general soundness condition
is the same one §6.2 satisfies trivially: a page's unit estimates must
be committed before the node learns anything about that page's actual
bytes. One-shot satisfies it by total silence. This variant satisfies it
per page, by commit-before-reveal ordering. Without the ordering, the
node could learn the actuals, put probability ≈ 1 on them, and collapse
every miss to ~1 bit.

The revealed bytes are incidental: the prover's own ciphertext, sent to
the prover's own node, the verifier seeing only Û. Their content does no
real work — conditioning later pages' estimates on earlier pages'
revealed bytes is likely near-worthless for KV, since bit-exact provers
have no misses, and actual KV values cannot correct the downstream
computation (the KV projections are not invertible, and under GQA the KV
width is far below the hidden width). What the reveal really carries is
one bit per page: *this page missed*.

When it would matter: the middle regime — misses rare enough that
proactive tables feel wasteful, yet common enough to occur — where
feedback spares the node from guessing which pages need tables, trading
a round trip for smaller PREDICT frames. The design regime
(~determinism, §5) is not that regime: honest provers miss nothing, so
the design proper takes one-shot, which is simpler for the interlock and
removes the challenge-time interlock→enclosure path entirely. If a real
deployment ever lands in the middle regime, this variant is the drop-in
answer.

## Appendix C — The selective-retention threat, analyzed

Status: threat analysis, not machinery. This appendix assesses the
selective-retention concern that motivated the restore rules. Its
conclusions are already reflected in the design (§6.4).

The concern: *the prover keeps packets with high unexplained information
content and crafts prompts that reload exactly those into the compute node
after a memory wipe.* How plausible is this?

**Without any restore rule: fully plausible, and worse than stated.**
If restores were unconstrained inbound traffic (as token requests are),
the store would be an uncommitted input channel. Worse, restores must be
admissible explanation inputs for token-side recomputation (else every
cache-served response fails its challenge) — so an unconstrained restore
is a *fabricated-input* channel: stage the covert output as a "restore,"
cite it as context, recompute predicts it perfectly, Û launders to zero.
This is exactly the attack input binding closes on the token side. So a
restore rule is not optional hardening — it is the storage analogue of
input binding, necessary for soundness.

**Under R1 alone: possible, but priced — and persistence is the real
novelty.** Walk the attack through:

1. The prover embeds covert content in write chunks. Each covert byte is
   committed at write time (P2) and sampled for recomputation with some
   probability `p_w` during its window. Sampled covert bytes score
   near-maximum surprisal.
2. Survival is content-independent — the prover cannot influence which
   writes get challenged, only observe it afterward. "Selectively keep the
   high-U packets" therefore means keeping the *lucky* ones, not steering
   the sample.
3. Budget arithmetic: to keep expected charges under budget rate `β`, the
   covert write rate is bounded by `β/p_w` bits per unit time. The
   surviving (never-challenged) covert stock then grows at ≤ `β(1−p_w)/p_w`.
4. After a chunk's 30-day window closes unchallenged, it is immune to
   write challenges forever, yet remains restorable forever — restores of
   it pass the R1 check by construction.

So under R1 the attack cannot exceed the information bound the protocol
already promises — the flow bound is intact. What it exploits is that
**storage converts the flow bound into a stock**: covert bits pay their
toll once (in expectation) and then persist as a permanent, reloadable
library. Re-writing old chunks does not re-expose them — a copy of a
committed chunk is explained by `D = identity` on committed data, so under
R1 the toll is genuinely once.

**Rule R2 changes the shape of the residual.** Under R2, a surviving
covert chunk is *not* immune: no prefill over any legitimate context
produces its bytes, so every restore of it faces a fresh `p_r` chance of
near-full charge against the accumulation budget. Surviving the write
window buys nothing. The library bleeds per use instead of persisting
free — charge per re-entry, not per install (§6.4). This is the
structural answer to the concern; R1's version is the priced-stock answer.

**No prompt-crafting is needed in the prover-adversary case.** The prover
controls the storage frontend and can restore anything at will. Crafted
prompts matter in two weaker variants:

- **Customer adversary.** A customer triggers restores only through
  prefix-cache hits on contexts they can name. Cross-tenant reloads
  require prover cooperation. Marginal.
- **Model adversary (T2).** This is where the write-recompute rule bites
  hardest. An honest-stack KV write is a deterministic function of
  (tokens, shape). The model cannot steer its own KV bytes except through
  tokens — which are visible and committed — or by deviating from the
  declared computation, which pays surprisal at write-sampling time. Under
  this addendum, **the model's persistent state is a deterministic
  function of committed token history.** "Notes to future self" outside
  that history cost budget at the moment of writing, and under R2 they
  cost budget again at every retrieval.

**Verdict.** The threat is plausible and the collaborator's instinct is
right: a restore rule is the necessary new control, and this addendum
specifies two. Levers against the residual, in order:

1. The R2 rate `p_r` — the structural fix: re-entry of banked state is
   priced per use.
2. The write-sampling rate `p_w` — shrinks the deposit rate that feeds the
   stock.
3. Lower storage-side budgets — honest shape-pinned provers achieve Û ≈ 0
   and need almost none.
4. A *store audit* extension — sampling resident chunks at rest — is
   sketched in §11, with its limits.
