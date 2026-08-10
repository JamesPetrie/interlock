# Addendum: KV-Offload Storage Tap (draft v0.3)

Extends `verification-protocol.md` (v6) to KV-cache offload traffic. Nothing in
the core protocol changes; this document specifies only the deltas. Design
decisions made where discussion left options open are marked **[D]**; genuinely
unsettled items are in [Open questions](#10-open-questions). Contested
feasibility claims from the external design note (`storage-tap-design-note.md`)
are engaged in §0 and §8.

Changes from v0.2: the tap now serves **two threats at two boundaries** —
exfiltration at the isolation boundary and **accumulation at the wipe
boundary** — and the primary challenge type moves from writes to **restores**,
scored against the restoring request's context. The v0.2 "enclosure fork"
(Branch A/B) is superseded: a fork remains, but it toggles only the write-side
charging policy and the tap's rate class, not the tap's existence.

## 0. Two boundaries, two threats

The design separates two nested perimeters around the compute cluster:

- The **isolation boundary** (the paper's verifier inference boundary):
  between the facility and the outside world. The threat here is
  **exfiltration** — information leaving. The token interlock sits on this
  boundary; the storage tap joins it only in deployments where the KV store
  is outside it (§0.2).
- The **wipe boundary**: between the cluster's *volatile* state (GPU HBM, host
  DRAM, device memory) and its *persistent* state (disk). The threat here is
  **accumulation** — information from previous prompts persisting and altering
  what the serving model computes. Volatile state is intermittently wiped;
  persistent state cannot be — so the storage tap sits on this boundary
  **always**, and its traffic is scored instead.

### 0.1 Why wipe memory but account disk

The split follows three asymmetries, each pushing the same direction:

1. **Erasure is verifiable only for volatile media.** A memory wipe is
   enforced by an externally observable physical act (power-cycling the
   enclosure); DRAM/HBM lose state by physics. Disk erasure is executed by
   drive firmware — prover hardware, adversarial under the threat model — so
   a faked disk wipe is invisible from outside.
2. **Observation is feasible only at the volatile/persistent interface.**
   Memory traffic runs at TB/s over chassis-internal buses: no tap point, no
   hashing hardware. Disk traffic crosses a discrete narrow link at GB/s:
   tappable and hashable.
3. **Destruction is affordable only for regenerable state.** Wiping memory
   costs a warm-up (restore from disk). Wiping disk destroys what disk exists
   for — including the prover's own 30-day challenge log and the weights —
   and so is incoherent with the protocol's storage obligations.

Composition gives the accumulation invariant: **to survive a wipe, state must
reach disk; to reach disk (and to return from it), it crosses the tap; what
crosses the tap is committed and its unexplained content is charged.**
Volatile accumulation dies periodically; persistent accumulation pays. (The
recomputation enclosure is wiped between challenge sessions per the core
protocol's Option 1 setup; the same reasoning applies there.)

**Physical prerequisites** for the wipe boundary to be real:

- **Diskless compute nodes.** No chassis-internal NVMe inside the wipe
  perimeter — a local drive is an untappable persistence site. Nodes netboot;
  all persistent storage sits on the far side of a tappable link.
- **Wipes cover all volatile state** — GPU device memory, NIC/DPU memory,
  SRAM buffers — enforceable by power cycle, externally observable.
- **The persistent tail** (BMC flash, firmware NVRAM) is small, enumerable,
  and handled by inventory/inspection — the same class of assumption the core
  protocol already makes for physical side channels, not a new kind of trust.

### 0.2 The remaining fork: where the store sits

Whether the KV store is inside or outside the **isolation** boundary is a
deployment choice (single-facility tier vs. cross-facility pooling). It
toggles exactly two things:

| | Store inside isolation boundary | Store outside (networked out) |
|---|---|---|
| Restore-scoring (accumulation, §7.1) | **required** | **required** |
| Write-scoring (exfiltration, §7.2) | not needed (writes never leave) | **required** |
| Tap rate class (§8) | sized by spill/restore rate | sized by pooling bandwidth |

Both logs (INWARD and OUTWARD) are committed in both cases — write commitment
is what makes restores cheap to predict and carries the pre-committed SHAPE
record — only the *charging policy* differs. The economics of pooling
(leveraged through `(1−f)`: a 5-point hit-rate drop at f = 0.80 is a 25%
prefill-compute increase, but plausibly small in practice since hot prefixes
replicate anyway) size the rate requirement and decide the write-scoring
column; they no longer decide whether the tap exists. **[Open — §10.1]**

A volume meter (byte conservation against token flow), considered in the
external design note as an alternative, is superseded on both branches:
restore-scoring is required regardless, subsumes the meter's accumulation
role, bounds *information* rather than volume (covert bytes at honest-KV
volume pass any conservation check), and needs no per-token KV constant —
which Option 1 cannot pin anyway. The note's topology-attestation proposal
survives as a complement (§9).

## 1. Scope

Serving stacks offload KV-cache state across the wipe boundary and restore it
later. This addendum extends the protocol to a **storage tap**: an interlock
on the compute↔store link committing both directions, plus new challenge
types scored by the existing recomputation interlock — **restore challenges**
(accumulation, always) and **write challenges** (exfiltration, when the store
is outside the isolation boundary).

The accumulation principle, owed to the framing that motivated this
extension: an honest KV dump is a deterministic function of the context
tokens and the model weights, so it contains no unexplained information;
recovering the context from a dump is a non-issue (the context is resubmitted
to the node anyway); therefore **unexplained information in a restored dump
is, definitionally, information accumulated from other, previous inputs** —
and that is the quantity the restore challenge measures.

## 2. Placement and directions

```
        wipe boundary                     isolation boundary
   ┌────────────────────────┐                    ┆
   │    prover compute      │                    ┆
   │  (volatile: HBM/DRAM,  │◀──────┐    ┌───────────┐
   │   wiped intermittently)│       │    │ KV store  │
   └──────────┬─────────────┘       │    │ (prover-  │
              │ writes (OUTWARD)    │    │  owned,   │
              ▼             restores│    │  never    │
        ┌───────────┐      (INWARD) │    │  wiped)   │
        │ storage   │───────────────┼────└─────┬─────┘
        │ tap       │◀──────────────┴──────────┘
        └───────────┘
```

- **OUTWARD** = compute → store: KV writes. Always committed; charged as
  exfiltration when the store is outside the isolation boundary.
- **INWARD** = store → compute: restores. Always committed and **scored**:
  a restore is a boundary crossing into the wipe perimeter, and its
  unexplained content is accumulated information re-entering the serving
  path.

The store is prover infrastructure, untrusted by both parties. A thin **store
shim** on the far side plays the frontend's role: canonical packets, bucket
stamps, the store side of the prover's log. **[D]** The shim carries no trust
assumptions — misbehavior degrades to drops or failed challenges, the
prover's own loss.

**Confidentiality is the production interlock's, unchanged.** All payloads
cross the tap as ciphertext under prover-held keys committed via `KEY_COMMIT`;
the tap hashes ciphertext only. A digest of AES-CTR ciphertext under a
per-chunk key supports no content inference — in particular no
prefix-confirmation against the plaintext block-hash schemes serving stacks
use internally. The verifier learns lengths, IDs, and timing (P5). The prover
switches on the storage link must encrypt at storage line rate (inline NIC
crypto at these rates is standard). Physical adjacency of a verifier device
to prover traffic is the same threat class as the production interlock beside
the prover switches, handled the same way.

## 3. Log and packet formats

One log per direction, same bucket/boundary-marker structure, same certificate
format and cadence. `DEVICE` distinguishes the tap's certificates; the
verifier anchors both chains independently.

**KV write packet** — canonical **request** format, unchanged at the header
level:

- `PLD_LEN`, `BUCKET`, `ID` — as in the core spec. `ID` strictly increasing
  across the storage session; `ID[0]` (inference flag) clear — **[D]** KV
  traffic is its own class, distinguished by device, not flag.
- `REFERENCE` — generalizes across the store (§4).
- `KEY_COMMIT` — key-material commitment for this chunk, fixed at write time;
  pins what key material may later enter the enclosure.

**Payload layout (encrypted):**

```
   +----------------+------------------------------------+
   |  SHAPE (S_sh)  |            DATA (KV bytes)         |
   +----------------+------------------------------------+
```

- **SHAPE** — fixed-width execution-shape record: batch size, padded sequence
  length, chunk-split offsets, a version/format word, and a reserved word for
  tensor-parallel rank/layout **[Open — §10.4]**. These are the scalars that
  (per the kv_noise measurements) fully determine the dump on a fixed stack.
  Riding inside the cert-bound payload, SHAPE is committed at write time and
  is therefore *pre-committed data*, admissible into the recomputation
  enclosure by construction.
- **DATA** — the KV bytes **as serialized for storage** — i.e.
  post-quantization (fp8 or whatever the stack's offload dtype is),
  position-addressable under the AES-CTR serialization rule (core Spec
  decision 3). The challenge object is the serialized ciphertext, so the
  noise floor that matters is the *post-quantization* one, not the raw-bf16
  floor kv_noise has measured so far **[Open — §10.3]**.

A chunk larger than `S_max_kv` spans packets chained by `REFERENCE` (the
long-output mechanism); only the first packet carries SHAPE. **Restore
packets** use the canonical **response** format, paired to the read request
that solicited them.

## 4. References across the store

Two new link types on the existing `REFERENCE` field:

- A **KV write** references the request whose prefill produced it.
- A **read request** (small OUTWARD packet) references the KV write it wants
  restored; the restore is its response.

Integrity rules are the existing ones (causality, cardinality, no fabricated
links). The write↔restore link is a **prover-side optimization, not an
integrity obligation**: it lets the prover locate the committed write (and
its SHAPE region) that cheaply predicts a challenged restore. Provenance is
deliberately *not* load-bearing for scoring — a restore is explained by what
its content is a function of, not by where it came from (§7.1). An unmatched
or unlinked restore is not a violation; it simply predicts poorly and pays.

## 5. Validity rules (storage tap)

Enforced on the fly, violations dropped whole:

- `PLD_LEN ≤ S_max_kv` (deployment-set, larger than inference `S_max`)
- `ID` strictly increasing OUTWARD across the session
- `ID` strictly increasing per bucket INWARD (out-of-order restores, mirroring
  production outbound)
- Drops leave no trace; compute notices via timeout and re-issues

## 6. Challenge mechanics (common to both types)

**Select and open** as in the core protocol: uniform `(bucket, byte)` over the
challenged log's capacity — the INWARD log for restore challenges, the OUTWARD
log for write challenges; a mid-chunk hit opens the whole reference-chained
chunk; standard opening plus the relevant request-side openings via the
reference walk.

**Recomputation** runs on the existing recomputation interlock and enclosure —
same staging contract, slice commitment, certificate. The challenge type is a
type word in the CTRL marker **[D]**. Staging pacing: the bank admits
~320 KB/bucket, so a multi-MB chunk stages across many buckets — permitted,
the frontend holds the slice open.

**Why chunk scoring is simpler than the token loop.** The token path reveals
sequentially because token *i* conditions token *i+1*. A KV chunk has no
autoregressive structure — it is a deterministic function of inputs the node
already holds — so the node predicts the entire DATA region in one shot,
before any of it is revealed, and the interlock's job collapses to
commit-then-compare (§7.3).

## 7. The two challenge types

### 7.1 Restore challenge (accumulation — the primary type)

Samples the **INWARD** log. The question: is this restored dump a function of
the restoring request's context and the declared weights — or does it carry
information accumulated from elsewhere?

**Explanation rule.** The enclosure receives the *restoring* request's
committed context chain (tokens), the SHAPE region of the originating write
(staged by the prover from its records; cert-bound, hence admissible), and
the key material — and must regenerate the dump by prefill from tokens and
weights alone. **Matching a committed prior write is deliberately NOT
sufficient explanation.** If it were, the control would collapse: covert
state could be banked with one write (uncharged when the store is inside the
isolation boundary) and every restore of it would be "explained" by
provenance. Explanation is content-functional, not provenancial.

Because the rule conditions on the *restoring* context, cross-request prefix
sharing works with no provenance tracking at all: a legitimate cache hit
means the current prompt contains the shared prefix that generated the dump,
so the dump is a function of the current context regardless of whose earlier
prompt wrote it.

**Charge semantics: per re-entry, not per install.** Unlike exfiltration
(where charging once at first exit is correct — the bits are already gone),
accumulated state is charged *every time it crosses back in* (at the sampling
rate). A covert blob banked on disk pays on the way out (if the store is
outside the isolation boundary), and pays again each time it is restored.
There is no pay-once-reuse-forever path through storage.

**Cost note.** A restore challenge is a cold prefill of the restoring context
— the prover cannot shortcut it by replaying stored bytes, by construction.
This is the price of the accumulation guarantee; it is bounded by context
length and paid only at the sampling rate. **[Open — §10.6]**

### 7.2 Write challenge (exfiltration — when the store is outside the isolation boundary)

Samples the **OUTWARD** log. The question is the paper's: how much
information in this egress is unexplained by declared computation on
committed inputs? The enclosure receives the originating request's context
chain, SHAPE, and keys, and predicts the written DATA. Scoring is §7.3;
charge is once, at first exit, against the exfiltration budget.

**Warm recomputation for token challenges (unchanged from v0.2, restated).**
For *token* challenges whose context chain bottoms out at a restore, the
committed restore blob remains admissible enclosure context (pre-committed on
the log before any challenge existed): recomputation runs warm, re-prefilling
only the uncached suffix. This does not launder accumulation: the admitted
blob is itself subject to restore-sampling under §7.1, so unexplained content
in it pays there, at the sampling rate. Cold-recompute cost multipliers (3–19×
the production serve at high hit rates) apply only to §7.1 restore challenges,
where cold is the point.

**Budget policy.** One Û mechanism, two meters: the verifier tracks the
OUTWARD (exfiltration) rate and the INWARD (accumulation) rate separately and
may set separate budgets — the harms are different and there is no reason to
let slack in one absorb violations of the other. **[D]**

### 7.3 Scoring: hierarchical digest estimate (both types)

DATA divides into fixed **pages** of `P` bytes (last short). After receiving
its staged inputs, the node sends one **PREDICT frame**: per-page digests of
its predicted ciphertext plus one committed probability `q` (the
estimate-frame custom float) — its confidence a page digest matches.

Per page, the interlock charges:

- **digest match:** `−log₂ q` (≈ 0 for q near 1)
- **digest miss:** `−log₂(1−q)` plus a per-unit pass over that page: one
  estimate frame per fixed-width unit (existing format, EOS entry omitted
  **[D]**), scored against the actual unit, then revealed. Reveals batch per
  page — no sequential dependence to protect — but a missed page's estimates
  are committed before any of its bytes are revealed, so the composite
  `Q(page) = q·[match] + (1−q)·Q_unit(page)` is a valid distribution and the
  Gibbs bound applies.

Both regimes are affordable: shape-pinned honest provers pay ~0;
honest-but-divergent provers pay noise-model surprisal only on divergent
pages at the σ kv_noise calibrates — never the 8·P full-entropy cliff.
`MAX_FALLBACK_PAGES` bounds challenge duration; pages beyond it charge as
misses with an all-catch-all unit pass. The cap is a liveness bound, not a
soundness concession.

**SHAPE accounting.** SHAPE is revealed to the enclosure before prediction,
so it cannot be predicted — charged flat at `8·S_sh` bits/chunk. **[D]**
~32 B per multi-MB chunk ≈ 10⁻⁵ of payload.

**No architecture pin needed.** Scoring is prediction-based and
architecture-agnostic: the prover recomputes whatever computation it actually
ran and either predicts its own bytes or pays. Nothing depends on a declared
per-token KV size or model architecture — consistent with Option 1's posture,
and the property that makes content commitment (rather than volume metering)
the viable mechanism here.

**Certificate:** unchanged — INWARD the challenge-slice commitment, OUTWARD
`(challenged ID ‖ Û)`, NONCE echoing the CTRL marker; verified against the
storage-log openings exactly as token challenges are against the production
log.

## 8. Hardware class and scaling path

Honesty section: the storage tap is the production interlock's *logic* but
not, at production rates, its hardware class.

- **Prototype (this repo):** MPF300 at 1 GbE. Topologically faithful —
  KV-over-Ethernet is how LMCache/Mooncake-class systems actually move
  chunks — but rate-toy. Valid as the logic, format, and challenge-flow
  demonstration; nothing more is claimed.
- **What the tap does NOT see:** the cache hierarchy's internal bandwidth.
  HBM↔DRAM traffic stays inside the wipe perimeter — wiped, not tapped. The
  tap sees only the **persistence/spill/share rate** across the wipe
  boundary. In a DRAM-rich deployment that is a small fraction of cache
  traffic; a deployment that keeps KV memory-only has near-zero OUTWARD KV
  traffic, and the tap is mostly an inbound provisioning channel. Note that
  in cache workloads the **restore (INWARD) rate typically exceeds the write
  rate** — reads are the point of caching — and INWARD is now the scored
  direction, so it sizes the commitment throughput.
- **Production rates:** per-node spill is `S_tok × offloaded prefill rate` —
  order 3 GB/s at 10⁴ tok/s fully offloaded for a 70B-class model; a
  fabric-level chokepoint is bounded by the link, ≤50 GB/s at 400 GbE.
  Line-rate hashing at these rates is demonstrated feasible on commodity
  hardware for large frames (single-EPYC SHA-256 >800 Gbps in isolated
  benchmark; AES-GMAC saturating 400 GbE at 8–16 cores at jumbo frames — and
  KV chunks are jumbo-frame traffic; the small-frame collapse regime does not
  apply). On an FPGA/ASIC tap this means parallel SHA lanes with a
  Merkle-tree combiner: 10–100× the token interlock's hashing work.
- **The real cost is auditability, not throughput.** A multi-lane hasher is
  no longer a "small state machine with no branching on packet contents."
  Mitigating structure: the lanes are identical replicas and the combiner is
  small, so the verification surface grows far more slowly than the
  throughput. State this trade wherever the formal-verification claim is
  made.
- **Keyed-MAC fallback [D, optional].** If cryptographic hashing at rate is
  the binding constraint on some deployment, SipHash/GMAC under the
  interlock's secret key suffices for binding *against the prover* (who lacks
  the key). Protocol cost: the prover can no longer recompute digests from
  its own logs, so the per-certificate byte-audit and self-served openings
  break — the verifier must recompute all openings. A real degradation of
  audit symmetry; default remains SHA-256.

## 9. Topology attestation

Independent of everything above, the tap should also attest, by sampled
header inspection at trivial cost, that every frame on the monitored fabric
has source and destination inside the declared device inventory. This hardens
the physical side-channel assumption the protocol leans on rather than
duplicating any commitment function. Autonomous cache-management DPUs
(BlueField-class) inside the boundary are opaque, network-capable
prover-firmware devices; every port on them belongs in the inventory.

## Accounting summary

| Traffic | Committed? | Charged? | Explained by |
|---|---|---|---|
| Restores (INWARD) | always | **yes — accumulation, per re-entry** | prefill from restoring context + weights (§7.1); provenance never sufficient |
| KV write DATA (OUTWARD) | always | iff store outside isolation boundary | digest match (shape-pinned) or per-unit noise model (§7.3) |
| KV write SHAPE | always | flat `8·S_sh` (when writes charged) | — (price of pinning) |
| Read requests (small) | always | iff store outside isolation boundary | — (non-inference traffic; tiny) |
| Storage-link timing/size | — | deferred | same status as core doc |

## Suggested parameters

`P` = 4 KB; `S_max_kv` deployment-set (≥ 1 MB); `S_sh` = 32 B fixed; page
digest = SHA-256 (one hash family device-wide, per core Spec decision 2);
`MAX_FALLBACK_PAGES` deployment-set; fallback unit width 16-bit, matching the
token path.

## 10. Open questions

1. **Pooling economics** (demoted from fork-decider to rate-sizer). How much
   hit rate does keeping the store inside the isolation boundary cost?
   Decides the write-charging column of §0.2 and the tap's rate class.
   Related: real-trace write amplification and restore/write ratio — they
   size honest traffic in both directions and thus challenge-sampling
   coverage.
2. **Divergent-chunk cost.** If shapes can't be pinned, frac_equal ≈ 0.2 at
   4 KB pages makes the per-unit fallback the dominant challenge cost. The
   dummy-row shape-pinning test (kv_noise follow-up #1) is load-bearing; run
   before freeze.
3. **Quantized serialization path.** The challenge object is fp8-serialized
   ciphertext; kv_noise has measured only raw bf16/fp16. Add the
   offload-dtype axis to the collector: does quantization absorb 1-ULP drift
   (widening bit-exactness) or introduce its own shape sensitivity? With
   sampling entropy committed away (Gumbel), cache-path drift is a *leading*
   Û term — this dataset sets the budget floor.
4. **TP-sharded writes.** Per-shard chunks add TP rank/NCCL layout to SHAPE.
   Unmeasured; out of scope until a multi-GPU testbed; SHAPE reserves a word
   now.
5. **Page size.** `P` = 4 KB is a guess; replay kv_noise divergence maps at
   several page sizes offline.
6. **Restore-challenge cost at long context.** §7.1 mandates cold prefill,
   which at agent-scale contexts (1M tokens) is ~20× the production serve
   per sampled challenge. Is there a sound intermediate — e.g., admitting
   context segments that have *themselves* passed restore challenges within
   this session — or is cold-at-sampling-rate simply the price? Needs a
   laundering analysis before any relaxation.
7. **Tap placement granularity.** Per-node vs per-cluster chokepoint changes
   the §8 rate target by ~an order of magnitude and interacts with where
   both boundaries are drawn — currently unpinned in the core doc as well.
8. **Wipe-cycle parameters.** Wipe period and its interaction with the
   challenge window and budget accounting (accumulation charged per re-entry
   makes the bound wipe-period-independent, but restore *volume* scales with
   wipe frequency, which feeds §10.1's rate sizing).
