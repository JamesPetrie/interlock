# Storage Addendum: KV-Cache Traffic Under the Interlock Protocol (DRAFT)

Status: discussion draft. This document extends `verification-protocol.md`
and `security-architecture.md` to a compute node with network-attached disk
storage. Nothing here changes the token-side protocol. This revision merges
the surviving content of the earlier draft (`old-storage-addendum.md`, v0.3)
and makes two structural decisions on top of it:

1. **Store placement is an assumption** The KV store sits
   inside the isolation boundary for the physical life of the media
   (see [Threat model](#threat-model)).
2. **Writes are logged, not challenged.** Challenge effort concentrates on
   restores — the point where accumulated content does harm
   (see [Restore challenges](#restore-challenges)).
3. **The read path is content-addressed** (folding in the first review
   round). A chunk fits in one packet, a restore is explained by
   exhibiting any equal-digest committed write, the SHAPE record rides
   a dedicated packet charged by estimate and reveal, and wipes are
   checked by a proof of secure erasure (see [Packet
   formats](#packet-formats), [Restore challenges](#restore-challenges)).

The empirical input is `experiments/kv_noise/` (determinism of honest KV
chunks).

## Terminology

This addendum inherits the vocabulary of `verification-protocol.md` and
`security-architecture.md`: interlock, certificate, log, bucket, opening,
challenge, the properties P1–P5, the unexplained-information bound U, and
the quarantine. New terms are listed below. Each term has one meaning, and
this document uses one phrase per concept throughout.

| Term | Meaning |
|---|---|
| **quarantine** | The monitored compute node and its volatile state (from `security-architecture.md`). Every byte that crosses its boundary passes a trusted device. Wiped at period `T_wipe`. |
| **isolation boundary** | The perimeter of the prover facility: one building, or a contiguous campus under a single inspection regime. The production interlock charges everything that crosses it. Disks never leave it (see [Topology and parties](#topology-and-parties), assumption 4). |
| **KV store** | The prover-owned disks behind the storage tap. They hold encrypted chunks and are never wiped. |
| **storage tap** | The trusted device in line on the compute↔storage link. A sibling of the production interlock, with its own certificate chain. "The tap" for short. |
| **storage frontend** | The untrusted prover software on the storage side of the tap. Distinct from the token-side prover frontend. |
| **chunk** | The unit of stored KV content: the DATA payload of one write packet. A chunk fits in one packet by definition; a larger KV region is written as several chunks at different addresses. |
| **write** | An OUTWARD packet that carries a chunk from the compute node to the KV store. |
| **read request** | A small OUTWARD packet that names the chunk to serve, in prover-defined terms (`CTX_REF` and `ADDRESS`). The naming is operational, not protocol-meaningful. |
| **restore** | An INWARD packet that carries a chunk from the KV store back into the compute node. |
| **accumulation** | Information from previous inputs that survives wipes on the KV store and later restores into compute. The one threat this addendum treats. |
| **accumulation budget** | The storage-side budget rate `β`: the unexplained bits per unit time the verifier tolerates in restores. Distinct from the token-side **exfiltration budget** (the existing U budget). |
| **matching challenge (R1)** | Standing restore challenge: the restored bytes must equal some committed prior write or commissioning-manifest artifact, exhibited by the prover. Digest comparison only. |
| **regeneration challenge (R2)** | Sampled restore challenge at rate `p_r`: the restored chunk must be regenerable by cold prefill from the restoring request's context. |
| **write audit** | Optional write-direction challenge at rate `p_w` (default ≈ 0): regenerate a chunk at write time. |
| **cold prefill** | Prefill from tokens and weights alone, with no stored KV admitted as input. |
| **prefill-shaped / decode-shaped** | Whether a chunk's bytes came from one-shot prefill kernels or from step-wise decode kernels. The two differ bit-wise (see [Determinism and the SHAPE record](#determinism-and-the-shape-record)). |
| **canonical rewrite** | Re-materializing a sequence's KV as a chunked prefill before offload, so the chunk is prefill-shaped. |
| **SHAPE record** | The fixed-width execution-shape record for an offloaded context. It rides in the SHAPE packet. |
| **SHAPE packet** | The write packet at `ADDRESS` = all-ones whose encrypted payload is a context's SHAPE record. Committed like any write. |
| **ADDRESS** | An opaque write-header field with prover-defined semantics (page index, object key). The tap hashes it and never interprets it. The all-ones value is reserved for the SHAPE packet. |
| **proof of secure erasure** | The wipe check: the compute node fills every enumerated memory with verifier-seeded pseudorandom data and answers with a MAC within a time bound. |
| **commissioning manifest** | The committed commissioning-time inventory: declared weights, stack images, the canonical KV serialization spec, the stack manifest, and the device inventory. |
| **page / PREDICT frame / unit table** | Scoring objects for regeneration: DATA divides into pages, and the recomputation node commits one PREDICT frame carrying per-page digests and optional per-unit estimate tables. |

Symbols:

| Symbol | Meaning |
|---|---|
| `U`, `Û` | Unexplained-information bound; its per-challenge estimate (core protocol). |
| `β`, `β·T` | Budget rate (unexplained bits per unit time on a direction); the corresponding stock over an operating period `T`. |
| `p_r` | Sampling rate of regeneration challenges on restores. Mandatory > 0. |
| `p_w` | Sampling rate of the optional write audit. Default ≈ 0. |
| `T_wipe` | Quarantine wipe period. Deployment parameter. |
| `C_st` | Per-bucket capacity of the storage link. |
| `S_max_kv` | Maximum storage payload length. Deployment-set, larger than the inference `S_max`. |
| `S_sh` | Size of the SHAPE record. |
| `P` | Page size for regeneration scoring. |
| `k` | Covered token length declared in a SHAPE record. |
| `q` | Per-page digest-match probability in a PREDICT frame. |
| `MAX_FALLBACK_PAGES` | Cap on the pages that may carry attached unit tables in one PREDICT frame. |

## Threat model

**The core protocol says nothing about a second cable.** The token
protocol bounds what leaves the compute node over the network. A
network-attached disk shelf is a second link. Such a disk is, in effect,
compute memory that survives wipes and never appears in any certificate.

**Assumption: the KV store stays inside the isolation boundary.** The
isolation boundary separates the prover facility from the outside world,
and the production interlock charges everything that crosses it. This
addendum assumes the disks sit entirely inside that boundary, for the
whole physical life of the media: no cross-facility KV pooling (see
[The KV store sits inside the isolation
boundary](#the-kv-store-sits-inside-the-isolation-boundary)), and no
drive ever leaves the facility intact (see [Topology and
parties](#topology-and-parties), assumption 4). Under this assumption
the storage link is not an exfiltration surface. Nothing that crosses it
can leave the facility. Exfiltration remains the production interlock's
problem alone, and this addendum treats one threat only.

**The threat: accumulation.** The quarantine's volatile state (GPU HBM,
host DRAM, device memory) is intermittently wiped. The KV store's
persistent state cannot be (see [Why wipe memory but account
disk](#why-wipe-memory-but-account-disk)). The threat is that
information from previous inputs persists on disk and alters what the
serving model computes: the prover keeps chunks with high unexplained
information content, then crafts prompts that restore exactly those
chunks into the compute node after a wipe. Appendix C analyzes this
attack. The adversary can also be the model itself.

**Accumulated content does harm only when it is restored.** This
consequence shapes the whole design. A byte that sits on a disk that can
never leave the facility, and never crosses back into compute,
influences nothing. The storage tap therefore sits on the
compute↔storage link, and the challenge machinery concentrates on the
INWARD (restore) direction. Writes are committed and logged — the log is
what restores are checked against — but writes are not challenged as
standing policy (see [Write challenges and the optional write
audit](#write-challenges-and-the-optional-write-audit)).

**Goal.** Extend the two invariants of `verification-protocol.md` across
the storage link, so that at every instant the compute node's state —
volatile *and* stored — is explainable as a declared computation on
committed inputs, up to charged unexplained-information budgets. The two
harms get two budgets, and slack in one must not absorb violations of
the other. The exfiltration budget stays where it was, on the token
side. The storage tap charges the accumulation budget: unexplained
content in restores (see [Restore challenges](#restore-challenges)).

## The storage invariant

The design reduces to one sentence:

> **The KV store is a delay line, not an input.** Every byte entering
> the quarantine from storage must equal a byte that previously left it
> as a committed write. And every restored byte must be explainable as
> what the declared computation produces *now*, from committed inputs.

The two halves of the sentence are the two restore challenges:

1. **The matching challenge (R1)** enforces the first half. A restore
   must equal some committed prior write (or a commissioning-manifest
   artifact), exhibited by the prover. This check is deterministic and
   nearly free: nothing uncommitted ever enters compute.
2. **The regeneration challenge (R2)** enforces the second half. A
   sampled restore must be regenerable by cold prefill from the
   restoring request's context. Unexplained content is charged against
   the accumulation budget.

Two consequences:

- **Enforcement sits at the point of harm.** The KV store may hold
  anything the compute node ever emitted — writes are logged, not
  challenged. What the design controls is the store's *effective*
  content: whatever crosses back into compute must match committed
  history (R1) and is charged for unexplained content when sampled
  (R2). By induction, compute state stays a function of committed token
  history plus the charged accumulation budget.
- **The (wipe, tap) pair extends the quarantine in time.** After a
  wipe, compute state = f(token inputs since the wipe, restores since
  the wipe). Restores are replays of committed writes, charged for
  accumulated content. So compute state at any time remains a function
  of committed history, up to the budget — the property the wipe alone
  cannot give once a disk exists.

Every challenge type below is an instance of the existing P3 statement,
"explained by a declared computation on committed inputs":

| Traffic | Declared computation `D` | Challenge mechanics |
|---|---|---|
| Write | KV prefill over a committed token context (split points per the SHAPE record) | Logged only; optional write audit |
| Restore, first half | **Identity** on an exhibited committed prior write | Digest comparison, no enclosure (R1) |
| Restore, second half | KV prefill over the *restoring* request's context | Cold prefill in the verifier enclosure (R2) |

Framing restores as recomputation keeps the protocol statement uniform:
R1 is recomputation with the cheapest possible `D`, and R2 aims the full
regeneration machinery at the one direction whose content can do harm.

## Budgets and scope

**Budget notation.** Throughout, `β` is a budget *rate*: the unexplained
information per unit time, in bits, that the verifier tolerates on a
direction before the prover is in violation. This is the storage-side
instance of the core protocol's standing budget — the prover must keep
its U-rate under budget, and every failed or unanswerable challenge is
charged against it (`verification-protocol.md`, "Two invariants").
Sampling makes the accounting statistical: challenges estimate the
rate, so an adversary pays in expectation, not per byte (Appendix C
works the arithmetic). Two consequences frame all budget policy here:

- Leftover budget is, by definition, tolerated covert bandwidth.
- The lower bound on `β` is empirical. It must sit above the honest
  prover's unavoidable surprisal. The kv_noise result (honest Û ≈ 0
  with pinned shapes, `experiments/kv_noise/README.md`) is what lets
  storage-side budgets sit near zero.

**The tap governs the link, not the disks.** More generally, the
protocol bounds information **flow** across the quarantine boundary. It
does not bound **possession** outside it. And the flow bound is not a
secrecy bound. The service continuously emits weight-dependent data —
every response token is some, and a KV chunk is nothing but activation
egress. The dividing line is therefore not *secret vs. known* but
**explained vs. unexplained**, relative to the declared computation and
the committed inputs:

- **Explained**: anything the declared computation produces from
  committed inputs — responses, KV chunks, activations. This crosses
  freely, at ~zero charge, by design. What the outside could or could
  not compute for itself is irrelevant to the accounting.
- **Unexplained**: everything else — the trace of any *other*
  computation (different weights, a fine-tune, cross-request
  aggregation, a covert payload). Charged against the budgets.

Stated positively: **U bounds the egress bandwidth of undeclared
computation.** The quarantine does not hide data. It *attributes
compute*: everything the monitored cluster emits is certified to be the
declared workload's output, give or take the budget rate.

**A guarantee requires scarce unmonitored compute.** The interlock makes
monitored compute attributable. Compute governance in the ordinary
sense — chip tracking, procurement, energy — must make unmonitored
compute small. The prover can run arbitrary computation on a side
cluster, but only at small scale, and only over bounded inputs:
declared outputs plus at most the charged covert allowance (`β·T` bits
over an operating period `T`). The protocol proves nothing about that
side cluster, and its end-to-end value degrades gracefully with the
ratio of verified to unverified compute. A corollary for budget policy:
the adversary's cheapest path to a prohibited artifact is hybrid —
heavy lifting on the monitored cluster, a small seed (LoRA deltas,
distillation targets, gradient sketches) through the charged channel,
finishing on the side cluster. So set `β` against the *smallest useful
artifact given the adversary's outside compute*, not against
full-artifact size.

**The cluster-side shim has nowhere to run.** The explained/unexplained
dichotomy disposes of a process that generates extra information on the
cluster and stores it without passing an interlock. Inside the
quarantine it has no persistence: memory is wiped, and there are no
local disks (see [Topology and parties](#topology-and-parties),
assumption 2). Outside the quarantine, the information must first
travel to it, and every path out crosses a committed link. The real
variant hides the extra bits *inside* valid traffic — token choices,
lengths, timing, KV bytes. That channel is not a gap. It is exactly the
unexplained channel the surprisal accounting charges, at the budgeted
capacity.

**An important limitation.** The protocol proves what
crossed, never what was destroyed. It cannot enforce erasure or
retention limits on data the prover legitimately handled — committed
inputs and explained outputs. That class of obligation needs a
different mechanism: trusted storage hardware, or contractual audit.

## Topology and parties

![Storage-extended prover-recomputation topology: the workload track is
unchanged. The compute node's storage link passes through a storage tap
into the prover-owned KV store. Challenged chunks route to the same
recomputation interlock and enclosure as token challenges.](fig-prover-recomp-storage.png){ width=100% }

New elements, mirroring the token-side trio (frontend / interlock /
node):

| Party | Trust | Role |
|---|---|---|
| **Storage tap** | Trusted device (sibling of the production core) | Sits in line on the compute↔storage link. Sanitizes, bucket-quantizes, commits both directions, emits its own certificate chain under its own `DEVICE` id. |
| **Storage frontend** | Untrusted prover software | The storage-side endpoint of the tap. Speaks canonical packets, calibrates buckets from sync packets, byte-audits certificates, logs traffic metadata, serves challenge openings, and manages the disks. |
| **KV store** | Untrusted prover hardware | The disks. They hold ciphertext chunks and are never wiped. |

**Direction convention.** The quarantine is the compute node, as
before. A write (compute → store) is OUTWARD traffic: information
leaving the quarantine. A restore (store → compute) is INWARD traffic.
The tap's certificate carries the same INWARD/OUTWARD commitment pair
as the production certificate, unchanged in format.

**Assumptions** (additions to the token-side list):

1. **Wipes.** The compute node's volatile memory is intermittently
   wiped, and the wipe covers *all* volatile state: GPU device memory,
   host DRAM, NIC/DPU memory, SRAM buffers. The wipe is checked by a
   **proof of secure erasure**. The concrete erasure protocol is open
   **[Open: proof of secure erasure](#open-items)**.
2. **Exclusive persistence path.** The tap line is the compute node's
   *only* path to persistent storage. Nodes are diskless and netboot —
   a chassis-internal NVMe would be an untappable persistence site. The
   small persistent tail (BMC flash, firmware NVRAM) is enumerable and
   handled by inventory and inspection — the same class of assumption
   the core protocol already makes for physical side channels, not a
   new kind of trust. This is the storage analogue of "the interlock is
   the only network path."
3. **Deterministic caching.** The prover can make its KV chunk bytes a
   deterministic function of (token context, execution shape). The
   Spark result in `experiments/kv_noise/README.md` supports this:
   every measured chunk was a pure function of tokens and kernel shape,
   with zero stochastic noise. More thorough determinism research (examining different hardware stacks) is
   still needed. [Determinism and the SHAPE
   record](#determinism-and-the-shape-record) handles the shape term.
4. **Storage custody.** No disk leaves the isolation boundary intact,
   over the media's entire physical lifetime. Failed, warrantied, and
   decommissioned drives are destroyed on site. The obligation covers
   bytes as well as platters: storage-side records that contain link
   payloads — the challenge log above all — are never replicated or
   backed up off campus, and the storage frontend and KV store sit in
   a network enclave with no route out of the campus. The protocol
   cannot check this — it is a custody obligation of the same class as
   the enclosure, enforced by inspection and contract. It is what
   makes unchallenged write content safe to leave uncharged: a covert
   write is inert unless its chunk is restored or its bytes walk out,
   on a platter or over a wire.

**Why the tap still needs full timing isolation.** The storage frontend
is prover-controlled, so sub-bucket write timing would be an
uncommitted channel from the quarantine to the prover's facility —
exactly what the production interlock exists to close. The tap
therefore keeps the full sibling construction: 802.3 sanitization,
exact-match bucket declaration, ping-pong bucket buffers, silent drops,
sender-directed syncs. Cost: about one bucket (1 ms) of added latency
per storage operation, which paged KV offload tolerates.

### Why wipe memory but account disk

The volatile/persistent split follows three asymmetries, each pushing
the same direction:

1. **Erasure can be checked only for volatile media.** DRAM/HBM lose
   state by physics, and a proof of secure erasure checks the wipe
   remotely (assumption 1). Disk erasure is executed by drive
   firmware — prover hardware, adversarial under the threat model — so
   a faked disk wipe is invisible from outside, and no analogous proof
   is affordable at disk capacity: filling the store to prove erasure
   would take hours and destroy what the disk exists to keep.
2. **Observation is feasible only at the volatile/persistent
   interface.** Memory traffic runs at TB/s over chassis-internal
   buses: no tap point, no hashing hardware. Disk traffic crosses a
   discrete narrow link at GB/s: tappable and hashable.
3. **Destruction is affordable only for regenerable state.** Wiping
   memory costs a warm-up (restore from disk). Wiping disk destroys
   what disk exists for — including the prover's own 30-day challenge
   log and the weights — and is incoherent with the protocol's storage
   obligations.

So: wipe what can be checked as wiped, and account what cannot.
Volatile accumulation dies periodically. Persistent accumulation pays.

### The KV store sits inside the isolation boundary

The old draft treated store placement — single-facility tier vs.
cross-facility pooling — as an open fork that toggled the
write-charging policy and the tap's rate class. This revision adopts it
as an assumption (see [Threat model](#threat-model)): the KV store
stays inside the isolation boundary, and cross-facility KV pooling is
excluded. The honest cost ledger:

1. **Serving efficiency: small.** Production KV pooling (Mooncake,
   NVIDIA Dynamo, LMCache, 3FS-class stores) is cluster- or
   datacenter-scoped, and GPU serving clusters are AZ-local, so the
   practice the assumption forbids is marginal today. KV chunks are
   GB-scale, shipping them over inter-facility links competes with
   local re-prefill, and cache-aware sticky routing already captures
   most reuse locally. Popular shared prefixes re-warm per campus by
   recomputation — legal, since no KV bytes cross the boundary. The
   residual is cache-cold spillover and failover: an AZ failover
   restarts with a cold cache, which is a warm-up cost, not data loss,
   since KV is regenerable. Estimate: low-single-digit percent of
   prefill compute. (The sensitivity the old draft quoted — a 5-point
   hit-rate drop at f = 0.80 is a 25% prefill-compute increase — is a
   sensitivity, not an expected operating point.) One contingency:
   this figure reflects current practice. If serving architecture
   moves toward cross-AZ KV disaggregation, the cost grows toward that
   sensitivity.
2. **A dedicated, campus-local store.** The KV tier cannot ride a
   regional managed storage service — regional replication is cross-AZ
   and opaque. The prover runs its own disk shelves behind the tap.
   This is mostly already implied by assumption 2 (the tap is the only
   persistence path), but it forecloses the cheapest storage option a
   cloud deployment has.
3. **Egress control on the storage enclave — the binding obligation.**
   The assumption is about bytes, not only platters (assumption 4). If
   the prover backs up its challenge log — the logged link payloads —
   to another site, covert write content leaves the campus over the
   backup channel, and the case for leaving writes unchallenged
   collapses. So the challenge log and every payload-bearing record
   stay on campus, durability is intra-campus only, and the storage
   enclave has no route out. This is an operational and compliance
   obligation, not a compute cost — the same enforcement class
   (inspection and contract) as the enclosure.
4. **Drive destruction.** On-site shredding voids RMA and resale
   value. Standard practice in high-security datacenters already;
   small money at fleet scale.

**What it buys.** Writes never cross the isolation boundary, so write
content needs no exfiltration charging at all. This is what lets the
design log writes without challenging them: the storage tap charges
only the accumulation budget, and the exfiltration budget remains the
production interlock's alone.

**Verdict: cheap in compute, real but familiar in operations.** The
serving-efficiency cost is small under current practice. The binding
costs are obligations — dedicated hardware, egress control,
destruction — of the same class the protocol already carries for the
enclosure and the device inventory. They add compliance surface, not
new machinery or new trust.

**A per-AZ stores assumption.** Cloud "regions" span several buildings — availability zones (AZs) — over
   metro distances, and region-scoped storage tiers cross between
   them. Widening the isolation boundary to region scope is not an
   option: the boundary must coincide with a physically controlled
   perimeter (one building, or a contiguous campus under a single
   inspection regime), and inter-AZ fiber crosses public ground the
   prover itself could tap — link encryption does not help, because
   the prover holds the keys. The fix is per-AZ stores: partition the
   storage tier so KV traffic stays inside each perimeter, and let
   cross-AZ reuse re-warm by recomputation, exactly as the
   cross-facility prohibition already requires. The remaining policy
   question is how large a campus may be and still count as
   inspectable **[Open: restore rate and budget policy](#open-items)**.

Standing obligations do not change with the assumption: both directions
are always committed, restores are always challengeable (R1 and R2),
and the tap's rate class is sized by the spill/restore rate (see
[Hardware class and scaling path](#hardware-class-and-scaling-path)).
A future cross-facility deployment would reintroduce the old draft's
write-exfiltration column (`old-storage-addendum.md`, §0.2) — and, with
it, standing write challenges — but it is out of scope here.

## Packet formats

The tap reuses the canonical 64-byte header. Field meanings shift,
header geometry does not, so `canon_proc`, `traffic_commit`,
`batch_buffer`, and `cert_build` run unmodified. The only build delta:
the reserved-zero check relaxes on the repurposed fields.

**Write packet (OUTWARD, compute → store)** — canonical request format:

```
         32 bit     32 bit          64 bit
      +----------+----------+---------------------+
      | PLD_LEN  |  BUCKET  |          ID         |
      +----------+----------+---------------------+
      |       CTX_REF       |       ADDRESS       |
      +---------------------+---------------------+
      |                                           |
      +-                KEY_COMMIT               -+
      |                                           |
      +-------------------------------------------+
```

**CTX_REF** \
Token-side transaction `ID` whose context this chunk caches. Names an
ID in the *production interlock's* chain (see [Cross-chain
plumbing](#cross-chain-plumbing)). Set to 0 if none.

**ADDRESS** \
Opaque field with prover-defined semantics — a page index, an object
key, whatever the storage frontend's layout uses. The tap hashes it
and never interprets it. The all-ones value is reserved: it marks the
SHAPE packet (below). That is the only value the protocol gives
meaning to. Every other value is opaque prover data, and no rule ever
reads one — in particular there is no "latest write at this address"
rule, which would require proving the absence of a later write.

**KEY_COMMIT** \
Commitment to the chunk's encryption key material, fixed at write time.
Same role as the token-side field: it pins what key material may later
enter the enclosure.

**Payload (encrypted):**

**DATA** \
The KV bytes **as serialized for storage**: post-quantization (fp8 or
whatever the stack's offload dtype is), position-addressable under the
AES-CTR serialization rule (core spec decision 3). The challenge object
is the serialized ciphertext, so the noise level that matters is the
*post-quantization* one, not the raw-bf16 level kv_noise has measured
so far. **[Open: serialization spec and the quantized
path](#open-items)**

**A chunk fits in one packet.** `PLD_LEN ≤ S_max_kv` caps chunk size
by construction; a larger KV region is written as several chunks at
different addresses. This matches how paged offload actually moves KV
(tens-of-KB layer pages), and it keeps every challenge opening a
single packet — no chained-chunk openings exist. Restore-then-extend
linkage needs no header field either: the split points live in the
SHAPE record, and regeneration is cold prefill over the covered prefix
regardless.

**SHAPE packet.** The write packet at `ADDRESS` = all-ones carries, as
its encrypted payload, the SHAPE record for the context named by its
`CTX_REF` (see [Determinism and the SHAPE
record](#determinism-and-the-shape-record)). It is committed at write
time like any write. At challenge time the prover exhibits the
governing SHAPE packet, and its charge is the estimate-and-reveal
exchange of [Scoring with hierarchical page
digests](#scoring-with-hierarchical-page-digests).

**Read request packet (OUTWARD, compute → store)** — a small
request-format packet whose reference fields carry a `CTX_REF` and an
`ADDRESS` naming the chunk to serve. The tap hashes both and
interprets neither. The naming is operational — it tells the storage
frontend what to serve — and carries no protocol meaning: the matching
challenge is content-addressed and never walks through these fields.
Naming by `(CTX_REF, ADDRESS)` rather than by tap-assigned write `ID`
also matters across wipes: a wipe erases whatever IDs compute had
assigned, but token-side IDs and addresses are reconstructible from
the request being served, so compute needs no memory of past writes to
issue reads.

**Restore packet (INWARD, store → compute)** — canonical response
format, paired to its read request by `ID`. The pairing is injective:
OUTWARD IDs are strictly increasing across the session, so a
read-request ID never repeats, and one restore per read request is a
challenge-time cardinality rule, mirroring the token side's
one-response-per-request rule. The prover therefore cannot choose,
after a challenge, which of several read requests a restore answers —
load-bearing for both the matching check and the timing estimate (see
[Restore challenges](#restore-challenges)).

**What the tap enforces.** Nothing new. The existing validity rules
apply: `PLD_LEN ≤ S_max_kv`, IDs strictly increasing OUTWARD across the
session, IDs strictly increasing per bucket INWARD (out-of-order
restores, mirroring production outbound), exact-match buckets, silent
whole-packet drops. Cross-direction and cross-chain causality
(the exhibited write earlier than the read request, `CTX_REF` ordering
per [Position order for mid-generation
writes](#position-order-for-mid-generation-writes)) are
*challenge-time* checks by the verifier, like the token-side
reference-chain rules. The tap just hashes the fields. This keeps the
tap a pure configuration of the production core.

**Encryption and confidentiality.** Chunks are encrypted with a
deterministic, position-addressable scheme (AES-CTR with a per-chunk
committed nonce), the storage analogue of core spec decision 3.
Consequences: the KV store holds only ciphertext, restore-vs-write
matching works on ciphertext digests without any key, and enclosure
scoring stays in ciphertext space (P5: the verifier never sees KV
plaintext — which matters, since KV contents leak user data and model
internals). A digest of AES-CTR ciphertext under a per-chunk key
supports no content inference — in particular no prefix-confirmation
against the plaintext block-hash schemes serving stacks use internally.
The prover's storage NICs must encrypt at line rate (inline NIC crypto
at these rates is standard). Physical adjacency of a verifier device to
prover traffic is the same threat class as the production interlock
beside the prover switches, handled the same way.

## Determinism and the SHAPE record

The question: how much extra information must the prover convey for
regeneration to work, and where does it live in the protocol?

**What the data says.** From `experiments/kv_noise/`: on a fixed stack,
chunks are bit-deterministic given (tokens, execution shape). Any shape
change — batch geometry, chunk splits, padding — seeds 1-ULP diffs that
amplify across layers until most elements differ. So regenerated chunks
are either bit-exact or hugely divergent. Bit-exact regeneration
requires reproducing the exact shapes.

**Prefill-shaped vs. decode-shaped chunks.** Prefill KV is one-shot
regenerable: the covered tokens are known, and one chunked prefill
reproduces the bytes. KV entries for *generated* tokens are not
produced that way: decode emits them one step at a time, with
decode-time kernel shapes, and per the kv_noise data decode-shaped
bytes differ from prefill-shaped bytes in most elements. A write that
offloads raw decode state therefore has three regeneration paths. They
apply to any challenge that regenerates a chunk — regeneration
challenges and write audits alike:

1. **Canonical rewrite (recommended).** Before offloading,
   re-materialize the sequence's KV as a canonical chunked prefill, and
   write that. Every committed chunk is then one-shot regenerable, and
   the SHAPE record stays at ~16–64 B. vLLM's recompute-on-preemption
   mode is this exact operation. The rewrite is not directly enforced,
   but **the accumulation budget should be set assuming the prover uses
   it.** The incentive operates at restore time: a regeneration
   challenge is itself a cold prefill, so prefill-shaped chunks are the
   cheap ones to defend.
2. **Decode replay.** Write decode-shaped bytes, log one batch-shape
   entry per decode step, and replay decode step-by-step at challenge
   time. Sound, but sequential, and the SHAPE record grows linearly
   with the generated length. Challenge-time policy must allow the
   replay time.
3. **Noise-model fallback.** Predict decode-shaped bytes from a
   one-shot prefill and pay the surprisal (Appendix A). The measured
   cross-shape divergence (frac_equal ≈ 0.2) makes this
   budget-infeasible at byte granularity. Listed for completeness, not
   viability.

**What must be conveyed, per write.** The SHAPE record:

| Field | Size | Note |
|---|---|---|
| Format/version tag | 4 B | pins the serialization layout |
| Padded sequence length | 4 B | doubles as the covered length `k` |
| Batch-size class | 2 B | dummy-row equality (kv_noise follow-up) means class, not contents |
| Chunk-split offsets | ~4 × 4 B | restore-then-extend split points |
| dtype / attn-backend enum | 2 B | |
| Engine build id | 8 B | hash into the committed stack manifest |
| Reserved | 4 B | tensor-parallel rank/layout **[Open: real serving stacks](#open-items)** |

<!-- REVISIT the field list above is pulled from the DGX Spark case;
review which fields are actually necessary before format freeze -->

Roughly **16–64 bytes per SHAPE packet, one per offloaded context**,
against a context's KV bytes of tens of KB to tens of MB — negligible.
A prover on path 1 above keeps every SHAPE packet in this regime — the
incentive-favored outcome. Only a prover on path 2 needs more: one
batch-shape entry per decode step, so its record grows linearly with
the generated length. There is also one
*per-deployment* artifact: the canonical KV serialization spec and the
stack manifest, committed at commissioning as part of the commissioning
manifest.

**One refinement under consideration: maximal-shape pinning.** The
prover fixes every shape parameter at one deployment-wide maximal
setting (pad every sequence, one batch class). The SHAPE record then
carries almost no information and could shrink toward the version tag
alone. The open question is the throughput cost of the padding. (An
earlier candidate refinement — predicting SHAPE from tokens and
charging its surprisal — is now the design: see the
estimate-and-reveal exchange in [Scoring with hierarchical page
digests](#scoring-with-hierarchical-page-digests).)

**Where the record lives — and why this is load-bearing.** The SHAPE
record is the encrypted payload of the SHAPE packet, committed via
`pld_digest` at write time like any write. It must **not** be supplied
loose at challenge time. A challenge-time shape blob is an input
chosen after the challenge is known: leave it unconstrained and the
prover puts the answer in it, the recomputation node "predicts"
perfectly, and Û is laundered to zero. This is precisely the
fabricated-input attack the core protocol closes with input binding.
Committing the record before any challenge exists (P2) closes it here.

**Which SHAPE packet governs.** The prover exhibits the governing
SHAPE packet at challenge time, under one rule: it must carry the
challenged chunk's `CTX_REF` and be committed before the read request.
Exhibiting leaves the prover a choice among any SHAPE packets it
pre-committed for that context, and the choice is self-limiting:
extracting `k` bits of post-challenge freedom requires `2^k`
pre-committed variants, and no shape choice can make honest prefill
emit covert content — shapes perturb ULPs, they do not encode
arbitrary bytes. The record is size-bounded by format, and its charge
is the estimate-and-reveal exchange of [Scoring with hierarchical page
digests](#scoring-with-hierarchical-page-digests).

**Confidentiality.** The SHAPE record is revealed into the enclosure
at challenge time in ciphertext space, like any payload. The verifier
never sees it. Shapes, hardware,
and stack details stay prover-private (P5), matching the kv_noise
conclusion that shape recording is a prover-side optimization the
incentive structure enforces for free.

**Fallback.** If a serving stack's shapes cannot be pinned (FP atomics,
NCCL reduction order), the prover's Q can absorb the divergence as paid
surprisal through noise-model estimates. This machinery would be
**new** — the core protocol offers only a token-side precedent (the ZKP
logit-noise model). Appendix A sketches a potential noise model for the
storage case — an open direction, not near-term machinery. At measured
divergence its cost is likely budget-breaking when divergence is
widespread, so treat the noise model as a safety valve for occasional
divergence, not an alternative to shape pinning. The recomputation node
is prover hardware inside the enclosure, so the prover's primary option
stays simpler: match the serving SKU and stack.

## Challenges

Sampling is unchanged in form: uniform `(bucket y, byte x)` over
`[B − window, B] × [0, C_st)` per direction, where `C_st` is the
storage link's per-bucket capacity (≈12.5 MB per 1 ms bucket at
100 Gb/s). Uniform over capacity keeps every transmitted byte equally
likely to be challenged, and an `x` past the bucket's content stays a
liveness check. Standing challenges sample the INWARD (restore) log.
The OUTWARD (write) log is sampled only where the write audit is
enabled. All storage charges land on the accumulation budget. The
challenge type rides as a type word in the CTRL marker (below).

**Staging pacing.** The challenge slice enters the enclosure through
the recomputation interlock's bucket buffer, which releases a bounded
volume (bank capacity, ~320 KB) per 1 ms bucket. A multi-MB slice
therefore cannot stage within one bucket — it streams across many
consecutive buckets (~100 ms for a 30 MB slice). This is permitted: no
rule requires a challenge slice to fit one bucket or one certificate
period. The staging session simply stays open across bucket
boundaries — the storage frontend keeps sending the slice's packets,
the ingress commitment keeps folding them in, and scoring begins only
after the full slice and the CTRL marker have arrived. The bucket
machinery paces throughput; it does not fragment the challenge.

**The CTRL marker** is inherited from the core recomputation flow
(`recomp_ilock_core.md`, `recomp_feed.md`): a header-only `ID = 0`
canonical packet the frontend stages between the context and the
challenged object. In the core design it does three jobs at once. Its
`KEY_COMMIT` field carries the challenge nonce, which the recomputation
certificate echoes as its NONCE. It arms the interlock's capture of the
challenged object. And its forwarding into the enclosure is the
recomputation START trigger. The storage delta adds a fourth job: a
**challenge-type word**, carried in the marker's otherwise-unused
`REFERENCE` field — 0 for a token challenge (backward compatible),
distinct values for a write audit and a regeneration challenge — so the
enclosure knows whether to run decode scoring or chunk regeneration.
The type word is operational, not soundness-bearing: the marker is part
of the staged slice, so the challenge-slice commitment attests it, and
the challenge's true type is anyway determined by the challenged ID's
device tag and direction. A mis-declared type just produces mismatched
predictions — a failed challenge, charged as usual.

### Write challenges and the optional write audit

Writes are always committed and logged. The log is what the matching
challenge (R1) checks restores against, and it carries the SHAPE
packet commitments the prover later exhibits. But under this
addendum's assumptions, writes are **not challenged as standing
policy**.

The reasoning: a write leaves the quarantine but stays inside the
isolation boundary. Under the storage-custody assumption its bytes
cannot leave the facility, so unexplained write content is not
exfiltration. And it is not yet accumulation harm either — a covert
write is inert until its chunk is restored, and the regeneration
challenge (R2) charges exactly that event, per restore. Charging the
write as well would be defense in depth against a boundary the
assumption already closes. Dropping standing write challenges also
removes real machinery: the challenge-window immunity problem (a chunk
that survived its window unchallenged became immune forever —
Appendix C), payload-retention rules for deleted chunks, and most of
the commitment-order inversion problem for mid-generation writes (see
[Position order for mid-generation
writes](#position-order-for-mid-generation-writes)).

**The write audit.** The mechanism is retained as an audit knob at rate
`p_w`, default ≈ 0. It mirrors the token-side Option 1 flow:

1. **Select** a byte in the OUTWARD (write) direction. The opening
   identifies the covering write packet — one packet is the whole
   chunk.
2. **Open.** Storage-tap certificate, bucket digests, records, the hit
   packet's header + `pld_digest` — plus the referenced context: the
   `CTX_REF` token-side chain opened against the production
   certificates, and the exhibited SHAPE packet opened against the
   tap's own chain. Ordering is checked in token-position order, not
   packet-commitment order. Every link must be committed. A missing
   link degrades to U, never breaks soundness.
3. **Stage.** The storage frontend stages into the enclosure, through
   the recomputation interlock: the token-context ciphertext and key
   material (checked against the token-side and write-side
   `KEY_COMMIT`s). The SHAPE packet stages to the interlock as a
   captured object, for the estimate-and-reveal exchange. The ingress
   commitment covers every byte, and the verifier checks it against
   the opened certificates. Nothing uncommitted enters.
4. **Score.** The recomputation node regenerates the chunk and predicts
   the *entire* DATA region in one shot, per [Scoring with hierarchical
   page digests](#scoring-with-hierarchical-page-digests).

What the write audit buys is **detection latency, not soundness**: it
can catch a covert-writing prover or model at write time, possibly long
before any restore, where R2 alone detects only on use. A deployment
that wants that telemetry — or that trusts the storage-custody
assumption less — turns `p_w` up. The budgets do not depend on it.

### Scoring with hierarchical page digests

This section applies to all challenges that regenerate a chunk:
regeneration challenges and write audits.

**Why chunk scoring is simpler than the token loop.** The token path
reveals sequentially because token *i* is sampled: the recomputation
node cannot know it before estimating token *i+1*. A KV chunk has no
such structure as a prediction target. Whatever its shape history, its
bytes are a deterministic function of inputs the node already holds —
computed in one shot for prefill-shaped chunks, by step-wise replay for
decode-shaped ones. Either way the node completes its entire prediction
of the DATA region *before scoring begins*, and the interlock's job
collapses to commit-then-compare. DATA scoring is therefore
**one-shot**: the node sends a single PREDICT frame, the interlock
scores it silently against the captured chunk, and nothing flows back
into the enclosure after the SHAPE reveal (below). (An interactive
variant with per-miss feedback is recorded in Appendix B.)

**Frame sequence per restore challenge**, after the CTRL marker: one
timing estimate, one SHAPE estimate, the interlock's SHAPE reveal,
then the PREDICT frame. This mirrors the token challenge's sequence
(one length estimate, one timing estimate, then token estimates). A
write audit omits the timing estimate — a write pairs with no request.

DATA divides into fixed **pages** of `P` bytes (last short). After
receiving its staged inputs, the recomputation node sends one **PREDICT
frame** containing, per page:

- a digest of its predicted ciphertext, with one committed probability
  `q` (the estimate-frame custom float) — its confidence that the
  digest matches;
- optionally, for pages it distrusts (low `q`), an attached **unit
  table**: one estimate per fixed-width unit of that page (existing
  estimate format, EOS entry omitted). How a prover would populate unit
  tables without pinned shapes — e.g. a noise model over its own
  divergence — is a hypothetical open direction, sketched in
  Appendix A, not part of the near-term design.

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
~determinism: an honest shape-pinned prover predicts bit-exactly,
attaches no tables, and misses nothing, so a feedback channel would
optimize a case that is not supposed to occur. The extremes need no
feedback either way — a bit-exact prover has no misses, and a
widely-divergent prover attaches tables everywhere. Only the middle
regime (rare, unpredictable misses) would benefit; Appendix B records
the interactive variant for it.

Shape-pinned honest provers pay ~0. Honest-but-divergent provers pay
per-unit surprisal only on divergent pages whose tables they
attached — never the `8·P` full-entropy cliff. `MAX_FALLBACK_PAGES`
caps how many pages may carry attached tables, bounding the PREDICT
frame and the interlock's scoring work: missed pages beyond the cap are
charged with the all-catch-all pass. The cap is a liveness bound, not a
soundness concession.

**SHAPE is charged by estimate and reveal.** The SHAPE record is not
handed to the enclosure as a free staged input — that would convey
`8·S_sh` bits uncharged — and charging it flat would put ~256 bits of
unavoidable surprisal into every honest challenge. Instead the
exchange reuses the token loop's frames. The recomputation node first
commits a SHAPE estimate: a distribution over the SHAPE packet's
ciphertext units, built from the staged context. The prover pre-loads
its predictor into the recomputation node — a static histogram of the
deployment's shapes already works, and padded length is nearly
deterministic given the tokens, so the honest residual is mostly the
batch class, a few bits. The interlock then reveals the captured SHAPE
packet's units, charges the estimate's surprisal, and regeneration
begins with the true shapes. The node holds the key, so estimates stay
in ciphertext space (P5 intact). Prediction quality tunes only the
honest cost: a node that sends no SHAPE estimate, or a malformed one,
is charged the flat `8·S_sh` worst case, so the shape channel can
never convey uncharged bits. Keeping the honest charge at a few bits
is what lets the honest surprisal floor — and therefore `β` — sit near
zero.

**Timing is charged the same way.** The timing estimate is a committed
distribution over the bucket difference between the read request and
the restore, scored against the actual committed buckets, which the
openings and the staged slice attest. Both endpoints are committed in
the tap's chain, and the restore pairs with exactly one read request,
so the quantity is well-defined. This un-defers exactly one timing
channel — read-to-restore latency, an inbound channel from the storage
frontend into compute. All other timing and size accounting keeps the
core protocol's deferred status.

**No architecture pin needed.** Scoring is prediction-based and
architecture-agnostic: the prover regenerates whatever computation it
actually ran and either predicts its own bytes or pays. Nothing depends
on a declared per-token KV size or model architecture — consistent with
Option 1's posture, and the property that makes content commitment the
viable mechanism here.

**Certificate.** Unchanged — INWARD the challenge-slice commitment,
OUTWARD `(challenged ID ‖ Û)` with a device tag disambiguating which
chain the ID lives in, NONCE echoing the CTRL marker. Checked against
the storage-log openings exactly as token challenges are against the
production log.

### Position order for mid-generation writes

Serving schedulers preempt running requests under memory pressure:
mid-generation, a sequence's KV cache — prompt KV plus the entries for
response tokens generated so far — is evicted to make room and copied
back on resume (vLLM's "swapping" preemption). With no compute-local
persistence, that eviction is a write across the tap, and its bytes
depend on response tokens that have not yet left the quarantine — the
write is committed *before* the tokens that explain it. With writes
unchallenged, this inversion is mostly moot: nothing regenerates the
chunk at commitment time. One rule remains, for any later challenge
that regenerates the chunk (a regeneration challenge or a write audit):
explanation order is **token-position order, not packet-commitment
order**. A chunk declares its covered length `k` in the SHAPE record.
It may cite token positions ≤ `k` as inputs. It may itself explain only
positions > `k`. Position indices give a well-founded order, so the
explanation graph stays acyclic, which is what soundness requires.

### Restore challenges

Restores are the boundary crossing *into* the quarantine, typically the
dominant traffic direction — reads are the point of caching — and, with
writes unchallenged, the design's entire standing challenge surface.
Two challenge types are defined. They check different things and
compose.

**R1 — the matching challenge (validity floor).** The restore must
equal a committed prior write.

1. **Select** a byte in the INWARD (restore) direction.
2. **Open the restore.** The storage frontend reveals the header and
   `pld_digest` of the restore packet that covers the selected byte.
   The verifier checks them against the tap's committed chain (bucket
   digests in the storage certificates). This proves the restore is a
   packet that actually crossed, not one constructed for the challenge.
3. **Pair with the read request.** The restore's `ID` names exactly
   one read request: OUTWARD IDs are strictly increasing across the
   session, and one restore per read request is a challenge-time
   cardinality rule (see [Packet formats](#packet-formats)). The
   prover cannot pick among several requests after the fact.
4. **Exhibit the matching write.** The storage frontend exhibits, from
   its own private index, a committed write earlier than the read
   request, and reveals its header and `pld_digest` against the same
   tap chain.
5. **Check**: `pld_digest(restore) = pld_digest(write)`, and the
   write's `ID` precedes the read request's.

**Existence is all the invariant needs.** The delay-line invariant
says every restored byte must equal a byte of *some* committed prior
write. Any committed write with an equal `pld_digest` carries a
byte-identical payload, so prover choice among several matching writes
is harmless, and no named pointer is required. Earlier drafts had the
read request carry a `WRITE_REF` field naming the write;
content-addressed exhibition replaces it. The pointer added nothing to
soundness, the storage frontend keeps whatever private index it likes,
and compute could not have retained tap-assigned write IDs across
wipes without extra inbound plumbing.

No enclosure, no regeneration, no payload transfer — only a few hashes.
R1 is what makes the delay-line invariant *hard*: nothing uncommitted
ever enters compute, deterministically, not merely charged. Run it at a
high sampling rate; it is nearly free. Two deliberate non-requirements:
restores need not be "justified" (which chunk, when, is a free
prover-to-compute channel — inbound freedom was never restricted, only
injection of uncommitted content), and restores need not match the
chunk the session semantically "wanted" (serving committed chunk Y
where X was wanted injects nothing new, and a response generated off
wrong KV mispredicts at token-challenge time and is charged there).

**Matching extends to provisioning inbound.** Not everything the KV
store serves is KV: diskless nodes pull weights, engine images, and
boot artifacts through the same tap, and the regeneration explanation
cannot apply to them — weights are not a function of any context. Their
explanation is provenance: the restore must match an artifact committed
in the commissioning manifest (declared weights, committed stack
images). R1's matching target is therefore a union: committed prior
writes, or commissioning-manifest artifacts. A restore matching a
manifest artifact is fully explained by identity and ends its challenge
there. A restore matching a prior write is KV-class and feeds R2. A
restore matching neither fails R1. This is a second reason the write
log stays: matching machinery must exist for the provisioning class
regardless, and extending it to KV writes is nearly free.

**R2 — the regeneration challenge (accumulation charge).** The restore
must be what an honest computation would produce *now*, for the request
being served. The principle, owed to the framing that motivated this
extension: an honest KV chunk is a deterministic function of the
context tokens and the model weights, so it contains no unexplained
information. Therefore **unexplained information in a restored chunk
is, definitionally, information accumulated from other, previous
inputs**. That is the quantity R2 measures.

Mechanics: the enclosure receives the *restoring* request's committed
context chain and the key material, and the interlock captures the
exhibited SHAPE packet (matching `CTX_REF`, committed before the read
request — see [Determinism and the SHAPE
record](#determinism-and-the-shape-record)). The node must regenerate
the chunk by cold prefill from tokens and weights alone, scored per
[Scoring with hierarchical page
digests](#scoring-with-hierarchical-page-digests) — timing estimate,
SHAPE estimate and reveal, then the PREDICT frame. Provenance is
deliberately **not** sufficient explanation under R2 — explanation is
content-functional. Two properties follow:

- **Charged per restore, not per write.** Exfiltration is correctly
  charged once, at first exit — the bits are already gone. Accumulated
  content is charged *every time it is restored*, at the sampling rate
  `p_r`. A covert chunk pays nothing at write time — writes are
  unchallenged — and pays on every sampled restore, which is where the
  harm is. There is no pay-once-reuse-forever path through storage
  (Appendix C).
- **Prefix sharing needs no provenance.** Because the rule conditions
  on the *restoring* context, a legitimate cache hit means the current
  prompt contains the shared prefix that generated the chunk — so the
  chunk is a function of the current context regardless of whose
  earlier prompt wrote it, across requests, tenants, and taps.

Costs: a regeneration challenge is a cold prefill of the restoring
context — the prover cannot shortcut it by replaying stored bytes, by
construction. At agent-scale contexts this is ~20× the production serve
per sampled challenge, on the high-rate direction.
**[Open: regeneration cost at long context](#open-items)**
Decode-shaped chunks interact per [Determinism and the SHAPE
record](#determinism-and-the-shape-record): canonical rewrite keeps R2
affordable, decode replay is the fallback.

**Status.** Both challenge types run. R1 is the validity floor, at high
rate, nearly free. R2 is mandatory: with writes unchallenged,
`p_r = 0` would leave stored covert content entirely uncharged — R2 is
the storage system's only content check. What remains open is the rate:
`p_r` trades cold-prefill cost against estimator variance and detection
latency (Appendix C works the arithmetic).
**[Open: restore rate and budget policy](#open-items)**

### Cross-chain plumbing

Two (or more) certificate chains now exist: production interlock(s) and
storage tap(s), each under its own `DEVICE` id, each independently
nonce-anchored. The verifier already keeps per-device anchor logs, so
each chain maps to wall-clock intervals within a skew bound.
Cross-chain causality checks compare those intervals with the skew
margin, subject to the position-order rule. Challenged-ID encodings in
recomputation certificates carry the device tag, so
`(DEVICE, direction, bucket, ID)` is globally unique.

Two symmetric cross-links:

- **Storage → token:** a write's `CTX_REF` opens against the production
  chain.
- **Token → storage:** a *token-side* response challenge may stage
  committed chunks as part of its explanation, under the same ingress
  rules — the natural path when serving used chunked, cache-restored
  prefill and bit-exact re-prefill requires replaying those exact
  splits.
  <!-- REVISIT the token→storage staging path needs a more in-depth
  review -->

**Warm recomputation for token challenges.** For token challenges whose
context chain bottoms out at a restore, the committed restore chunk is
admissible enclosure context (committed on the log before any challenge
existed): recomputation runs warm, re-prefilling only the uncached
suffix. This does not launder accumulation: the admitted chunk is
itself subject to restore sampling, so unexplained content in it pays
there, at the sampling rate. Cold prefill applies only to regeneration
challenges, where cold is the point.

**Shared store across compute nodes.** A restore on tap B may be
explained by a write committed on tap A (cross-replica prefix-cache
sharing). The exhibited write then opens against tap A's chain,
ordered against tap B's read request by the anchored wall-clock
intervals, and R2 needs no provenance at all. No new machinery.

## Shared recomputation infrastructure

Verdict: share it. Storage challenges use the same recomputation
interlock and the same recomputation node as token challenges.

- **Same scoring machinery.** Commit-before-scoring, sub-distribution
  check, surprisal accumulation, certificate format — all the same. The
  deltas are frame-level conventions, not new blocks: the PREDICT frame
  with attached unit tables, the estimate format's EOS entry omitted, a
  challenge-type word in the CTRL marker, and the device tag in the
  challenged ID. Storage challenges use the reveal path exactly once —
  the SHAPE reveal after the SHAPE estimate — and DATA scoring is
  one-shot: a single PREDICT frame, with nothing else sent into the
  enclosure.
- **Same node assets.** Storage challenges run prefill with the same
  weights and engine that token challenges use. A dedicated storage
  node would hold identical secrets and need identical enclosure
  treatment.
- **Same trust argument.** One audited device design, one enclosure
  boundary, one session-wipe discipline. Duplication doubles the TCB
  surface for zero soundness gain.
- **Load is fine.** Challenges are rare and serialized by verifier
  policy. Staging a long context (up to ~GB) over the enclosure link
  adds ~10 s at 1G — acceptable at challenge cadence, and an argument
  for a faster enclosure link, not a second device.

Two conditions make sharing sound, both already required:

1. **Session isolation.** Enclosure memory is wiped between challenge
   sessions, and a reset mid-session aborts, never resumes.
2. **The KV store stays outside the enclosure.** The recomputation node
   reaches committed chunks only through the recomputation interlock's
   committed ingress. Any side path to the KV store would let the node
   replay covert chunk contents as "predictions" at probability 1.

## Hardware class and scaling path

Honesty section: the storage tap is the production interlock's *logic*
but not, at production rates, its hardware class.

- **Prototype (this repo):** MPF300 at 1 GbE. Topologically faithful —
  KV-over-Ethernet is how LMCache/Mooncake-class systems actually move
  chunks — but rate-toy. Valid as the logic, format, and challenge-flow
  demonstration; nothing more is claimed.
- **What the tap does NOT see:** the cache hierarchy's internal
  bandwidth. HBM↔DRAM traffic stays inside the quarantine — wiped, not
  tapped. The tap sees only the **persistence/spill/share rate** across
  the quarantine boundary. In a DRAM-rich deployment that is a small
  fraction of cache traffic; a deployment that keeps KV memory-only has
  near-zero OUTWARD KV traffic, and the tap is mostly an inbound
  provisioning channel. Note that in cache workloads the **restore
  (INWARD) rate typically exceeds the write rate** — reads are the
  point of caching — and INWARD is a scored direction, so it sizes the
  commitment throughput.
- **Production rates:** per-node spill is `S_tok ×` offloaded prefill
  rate — order 3 GB/s at 10⁴ tok/s fully offloaded for a 70B-class
  model; a fabric-level chokepoint is bounded by the link, ≤50 GB/s at
  400 GbE. Line-rate hashing at these rates is demonstrated feasible on
  commodity hardware for large frames (single-EPYC SHA-256 >800 Gbps in
  isolated benchmark; AES-GMAC saturating 400 GbE at 8–16 cores at
  jumbo frames — and KV chunks are jumbo-frame traffic, so the
  small-frame collapse regime does not apply). On an FPGA/ASIC tap this
  means parallel SHA lanes with a Merkle-tree combiner: 10–100× the
  production interlock's hashing work.
- **The real cost is auditability, not throughput.** A multi-lane
  hasher is no longer a "small state machine with no branching on
  packet contents." Mitigating structure: the lanes are identical
  replicas and the combiner is small, so the verification surface grows
  far more slowly than the throughput. State this trade wherever the
  formal-verification claim is made.
- **Keyed-MAC fallback (optional).** If cryptographic hashing at rate
  is the binding constraint on some deployment, SipHash/GMAC under the
  interlock's secret key suffices for binding *against the prover* (who
  lacks the key). Protocol cost: the prover can no longer recompute
  digests from its own logs, so the per-certificate byte-audit and
  self-served openings break — the verifier must recompute all
  openings. A real degradation of audit symmetry; the default remains
  SHA-256.

## Topology attestation

**_REVISIT this section isn't reviewed in-depth. The DPU aspect
seems potentially important._**

A second, independent tap function, separate from commitment. The
**device inventory** — part of the commissioning manifest — is a list
of every device permitted on the monitored fabric, with its network
addresses. The tap samples frames and reads only the header address
fields (source and destination MAC/IP). It checks both addresses
against the device inventory and flags any frame whose endpoint is not
listed. The cost is trivial: no payload access, no cryptography,
sampled rather than exhaustive.

**What this hardens.** The protocol's soundness rests on physical
claims, not just cryptographic ones: the tapped links are the *only*
paths across each boundary, and only declared devices sit on the fabric
(see [Topology and parties](#topology-and-parties), assumptions 1–2,
and the token-side "the interlock is the only network path"). The
commitment machinery cannot check those claims — it charges the bytes
on the link it sees, and it is blind to an undeclared endpoint beside
it. A frame addressed to or from an unlisted device is direct evidence
of such an endpoint: a rogue NIC, an unlisted box, a possible
unmonitored path. That is why this check hardens the physical
assumption rather than duplicating any commitment role: it charges
nothing and commits nothing. It is a tripwire. A hit is a protocol
violation to investigate, not a budget charge.

**Why DPUs get special mention.** Real storage stacks offload cache
management to data-processing units (DPUs, e.g. NVIDIA BlueField): full
computers on a NIC, running prover firmware the verifier cannot
inspect, with their own network ports, able to originate traffic
autonomously. An unlisted DPU port is exactly the undeclared endpoint
this check exists to catch. So every port on every DPU inside the
boundary belongs in the device inventory as its own entry.

## Accounting summary and suggested parameters

| Traffic | Committed? | Challenged by | Charged against |
|---|---|---|---|
| Write DATA (OUTWARD) | always | write audit at `p_w` ≈ 0 | — covert writes are inert until restored, and charged there |
| SHAPE packet | always | estimate and reveal per challenged chunk; flat `8·S_sh` worst case | accumulation budget — a few honest bits |
| Read requests (OUTWARD, small) | always | — | — (tiny, non-inference, never leave the facility) |
| Restores (INWARD) | always | matching challenge (floor) + regeneration challenge at `p_r` | accumulation budget, per restore |
| Read-to-restore timing | always (buckets) | timing estimate, per regeneration challenge | accumulation budget |
| Other timing / size | — | deferred | same status as the core protocol |

Suggested parameters: `P` = 4 KB **[Open: page size](#open-items)**;
`S_max_kv` deployment-set (≥ 1 MB); `S_sh` fixed-width ≤ 64 B (32 B
typical); page digest = SHA-256 (one hash family device-wide, per core
spec decision 2); `MAX_FALLBACK_PAGES` deployment-set; fallback unit
width 16-bit, matching the token path; `p_r` and the budget split —
policy **[Open: restore rate and budget policy](#open-items)**; `p_w`
default ≈ 0 (write audit only).

## Open items

1. **Restore rate and budget policy.** Choose `p_r` (mandatory > 0 —
   the regeneration challenge is the storage system's only content
   check) and the accumulation budget `β`, charged per restore only,
   since writes are uncharged. Related inputs: real-trace write
   amplification and the restore/write ratio, which size honest traffic
   in both directions and thus sampling coverage. Store placement
   itself is decided; what remains empirical is the cost of that
   decision — measure the cache-cold spillover/failover fraction on
   real traffic — and the campus-size limit for an inspectable
   isolation boundary.
2. **Regeneration cost at long context.** Cold prefill is ~20× the
   production serve per sampled challenge at agent-scale (1M-token)
   contexts. Is there a sound intermediate — e.g., admitting context
   segments that have *themselves* passed restore challenges within the
   session — or is cold-at-sampling-rate simply the price? Needs a
   laundering analysis before any relaxation.
3. **Bucket-counter width.** The 32-bit bucket field wraps at
   ~49.7 days (known item), and restores may reference writes older
   than one wrap period, so long-horizon bucket addressing matters more
   here. The wrap/era policy should land with this addendum.
4. **Serialization spec and the quantized path.** The canonical KV
   serialization spec (layout, element width, chunk/nonce derivation)
   must be written and committed at commissioning. And the challenge
   object is post-quantization ciphertext, while kv_noise has measured
   only raw bf16/fp16: add the offload-dtype axis to the collector.
   Does quantization absorb 1-ULP drift (widening bit-exactness), or
   introduce its own shape sensitivity? This dataset sets the
   accumulation budget's empirical lower bound, and it decides whether
   the Appendix A noise-model fallback is usable at all — at current
   bf16 divergence its honest cost looks budget-breaking whenever
   divergence is widespread.
5. **Page size.** `P` = 4 KB is a guess; replay kv_noise divergence
   maps at several page sizes offline.
6. **Real serving stacks.** vLLM paged attention, continuous batching,
   and multi-GPU tensor parallel are the dominant real-world shape
   sources and are untested (SHAPE reserves a word for TP rank/layout).
   The dummy-row shape-pinning test (kv_noise follow-up #1) is
   load-bearing for the batch-size-class field; run it before format
   freeze.
7. **GC / TRIM semantics.** Deletes are ordinary committed OUTWARD
   control traffic and need no special rules. With no standing write
   challenges there are no open challenge windows on deleted chunks.
   What must persist is only the log metadata (headers and digests)
   that the matching challenge checks against, retained indefinitely
   anyway (item 3). A worked example should confirm nothing else leaks
   through deletion.
8. **Wipe-cycle parameters.** Per-restore charging makes the
   accumulation bound independent of the wipe period, but restore
   *volume* scales with wipe frequency, which feeds item 1's rate
   sizing.
9. **Tap placement granularity.** Per-node vs. per-cluster chokepoint
   changes the rate target of [Hardware class and scaling
   path](#hardware-class-and-scaling-path) by roughly an order of
   magnitude, and interacts with where both boundaries are drawn.
10. **Store audit (optional extension).** Sampling *resident* chunks at
    rest would need an enumeration commitment over KV store contents
    (e.g., a Merkle root the storage frontend maintains). The storage
    frontend is untrusted, so such an audit proves provenance of what
    it enumerates, never absence of hidden disk bytes. Hidden bytes
    that never cross the tap can never reach compute, so this buys
    assurance against sloppy provers, not strong ones. The regeneration
    challenge reduces its urgency; deferred until a stock-sensitive
    deployment asks for it.
11. **Proof of secure erasure.** The wipe check (see [Topology and
    parties](#topology-and-parties), assumption 1) needs a concrete
    protocol: the memory inventory it enumerates, the fill function,
    and the response time bound, derived from committed memory sizes
    and bandwidths. Its scheduling interacts with `T_wipe` and with
    the restore volume of item 8.
12. **Timing model.** The timing estimate charges read-to-restore
    latency, but the honest latency distribution is empirical —
    measure it on real offload traffic before setting budgets. Whether
    the OUTWARD side (write and read-request timing, a channel from
    the quarantine to the facility) also needs charging stays deferred
    with the core protocol's timing accounting.

## Appendix A: noise-model scoring (hypothetical fallback)

Status: an open direction, recorded for analysis — **not near-term
machinery**. The design proper assumes shape pinning: honest provers
predict bit-exactly, and the per-unit pass of [Scoring with
hierarchical page digests](#scoring-with-hierarchical-page-digests)
exists only to make occasional misses finite. This appendix asks what a
prover *without* pinned shapes would do. The split matters: the
per-unit estimate pass is protocol, while the noise model below is a
prover-side *strategy* for populating those estimate tables. Nothing
here changes the interlock.

**The sketch — token-side precedent only.** The core protocol contains
one noise model, and it is not this one: the ZKP instantiation pins a
hardware-noise model over *logits*,
`Q_t(o_t) ∝ exp(−(v* − ℓ_{o_t})²/s_c)` (`verification-protocol.md`).
That is a precedent for the idea — score a committed distribution
centered on the recomputed value — not a mechanism this addendum can
inherit. A KV instance would work as follows. In a unit table (attached
proactively under one-shot scoring), a unit is one serialized element.
The recomputation node predicts the element value `v̂` and commits a
discretized distribution around it: mass on `v̂` and its near-ULP
neighbors, `q(v) ∝ exp(−(v − v̂)²/2σ²)`, a handful of explicit entries
plus the catch-all. The node holds the key, so it maps each plaintext
candidate to its ciphertext word at that position (AES-CTR keystream),
and the committed table stays in ciphertext space — P5 intact. σ is not
a protocol constant. The prover fits it privately, per dtype and stack,
from its own divergence measurements (the kv_noise sweep gives
reference magnitudes: cross-shape bf16 shows frac_equal ≈ 0.2,
ulp≤1 ≈ 0.4–0.5). Soundness needs nothing from σ: the interlock checks
only that the table is a valid sub-distribution, so a wrong σ hurts
only the prover.

**Why it is not near-term: feasibility is quantitative, and unproven.**
At measured cross-shape divergence, per-element surprisal is order 1–3
bits, and a multi-MB chunk has 10⁶–10⁷ elements — order *megabits* per
sampled divergent chunk. The accumulation budget must sit above honest
surprisal (see [Budgets and scope](#budgets-and-scope)), so a prover
whose every chunk diverges is likely not deployable under any useful
accumulation budget. Read the noise model as a safety valve for
occasional divergence (a few missed pages), not a standing alternative
to shape pinning. Whether post-quantization serialization tightens σ
enough to change this verdict is the measurement of open item 4 — that
result decides whether this appendix graduates into the design or
stands as a negative result.

## Appendix B: interactive miss feedback (alternative frame design)

Status: a sound alternative to one-shot scoring, recorded because it
reuses the token loop's frames — not part of the design.

Under this variant the PREDICT frame carries digests and `q` only, with
no attached tables. The interlock signals each missed page by sending
that page's actual bytes in the token loop's reveal frame, and the
recomputation node answers with the page's unit estimates. The general
soundness condition is the same one one-shot scoring satisfies
trivially: a page's unit estimates must be committed before the node
learns anything about that page's actual bytes. One-shot satisfies it
by total silence. This variant satisfies it per page, by
commit-before-reveal ordering. Without the ordering, the node could
learn the actuals, put probability ≈ 1 on them, and collapse every miss
to ~1 bit.

The revealed bytes are incidental: the prover's own ciphertext, sent to
the prover's own node, the verifier seeing only Û. Their content does
no real work — conditioning later pages' estimates on earlier pages'
revealed bytes is likely near-worthless for KV, since bit-exact provers
have no misses, and actual KV values cannot correct the downstream
computation (the KV projections are not invertible, and under GQA the
KV width is far below the hidden width). What the reveal really carries
is one bit per page: *this page missed*.

When it would matter: the middle regime — misses rare enough that
proactive tables feel wasteful, yet common enough to occur — where
feedback spares the node from guessing which pages need tables, trading
a round trip for smaller PREDICT frames. The design regime
(~determinism) is not that regime: honest provers miss nothing, so the
design proper takes one-shot DATA scoring, which is simpler for the
interlock and keeps the challenge-time interlock→enclosure path to the
single SHAPE reveal. If a real deployment ever lands in the middle
regime, this variant is the drop-in answer.

## Appendix C: the selective-retention threat, analyzed

Status: threat analysis, not machinery. This appendix assesses the
selective-retention concern that motivated the restore challenges. Its
conclusions are already reflected in the design.

The concern: *the prover keeps chunks with high unexplained information
content and crafts prompts that restore exactly those into the compute
node after a memory wipe.* How plausible is this?

**Without any restore challenge: fully plausible, and worse than
stated.** If restores were unconstrained inbound traffic (as token
requests are), the KV store would be an uncommitted input channel.
Worse, restores must be admissible explanation inputs for token-side
recomputation (else every cache-served response fails its challenge) —
so an unconstrained restore is a *fabricated-input* channel: stage the
covert output as a "restore," cite it as context, recomputation
predicts it perfectly, Û launders to zero. This is exactly the attack
input binding closes on the token side. So a restore challenge is not
optional hardening — it is the storage analogue of input binding,
necessary for soundness.

**Under R1 alone: uncharged, so R1 alone is insufficient.** The
matching challenge confines inbound bytes to committed history, but
with writes unchallenged that history is free to poison. Walk the
attack through:

1. The prover embeds covert content in write chunks. Each covert byte
   is committed at write time (P2) but never challenged — the write
   costs nothing.
2. The chunks persist across wipes and are restorable forever. Every
   restore of them passes the matching check by construction.
3. Nothing anywhere charges the covert content. The KV store is an
   uncharged, permanent, restorable library.

This is why the regeneration challenge is mandatory: under this
addendum's assumptions, the restore direction carries the *only*
content check in the storage system.

**Under R1 + R2: charged per use, in expectation.** Under the
regeneration challenge, every sampled restore must be regenerable by
cold prefill from the restoring context. No prefill over any legitimate
context produces covert bytes, so a covert restore scores near-full
surprisal when sampled. The arithmetic: an adversary restoring covert
content at rate `r` bits per unit time is charged, in expectation, at
rate `r` — the sampled-surprisal estimator is unbiased, so the sampling
rate `p_r` sets variance and detection latency, not the expected
charge. Covert restore bandwidth is therefore bounded by the
accumulation budget `β`, *per use*: stored covert content is charged
every time it is restored — per restore, not per write.

The honest caveat is variance. With `p_r` small (cold prefill is
expensive), a gambling adversary lands on the order of `1/p_r` covert
chunks before the first sampled hit. The compensation: one hit is
unambiguous — a multi-MB covert chunk scores megabits against a
near-zero budget, an immediate and provable violation, not a marginal
overage. `p_r` therefore buys detection latency, and its lower bound
should be set from the damage a `1/p_r`-chunk burst could do before
detection.

**No prompt-crafting is needed in the prover-adversary case.** The
prover controls the storage frontend and can restore anything at will.
Crafted prompts matter in two weaker variants:

- **Customer adversary.** A customer triggers restores only through
  prefix-cache hits on contexts they can name. Cross-tenant restores
  require prover cooperation. Marginal.
- **Model adversary.** An honest-stack write is a deterministic
  function of (tokens, shape), so the model cannot steer its own KV
  bytes except through tokens — visible and committed — or by deviating
  from the declared computation. Deviant writes cost nothing at write
  time, but a note-to-self is useless until read back, and every
  restore faces the regeneration sample. The guarantee: **the model's
  persistent state that is restored into compute is a deterministic
  function of committed token history, up to the charged accumulation
  budget.** (The write audit strengthens this to write-time detection.)

**Verdict.** The threat is plausible and the collaborator's instinct is
right: a restore challenge is the necessary new control, and this
addendum specifies two. Levers against the residual, in order:

1. The regeneration rate `p_r` — the structural fix: restores of stored
   covert content are charged per use, and the rate sets detection
   latency.
2. Lower storage-side budgets — honest shape-pinned provers achieve
   Û ≈ 0 and need almost none.
3. The write audit `p_w` — write-time detection, for deployments that
   want the telemetry or trust storage custody less.
4. A store audit extension — sampling resident chunks at rest — is
   sketched in open item 10, with its limits.
