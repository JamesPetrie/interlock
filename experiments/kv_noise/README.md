# kv_noise — honest KV-dump noise dataset

Measures how much honest KV-cache dumps vary run-to-run on real serving
hardware. This is the make-or-break empirical input for the storage
traffic logging design. Under prover recomputation (paper Option 1), the
prover commits a prediction Q of its own dump and pays the surprisal
−log₂ Q against a fixed unexplained-information budget — so the honest
noise floor is the bill an honest prover can't avoid paying, and it
determines how low the verifier can set the budget (leftover budget is
covert-channel bandwidth). See the long docstring in
`collect_kv_dumps.py` for the full background (what prefill is, what KV
tensors look like, why prefill has no RNG and the noise is purely
reduction-order/kernel-selection).

**Collection needs the hardware; analysis doesn't.** Over-collect on the
Spark, copy everything off, analyze later on a laptop.

## Setup (on the Spark)

```
python3 -m venv .venv && source .venv/bin/activate
pip install torch transformers safetensors
```

Default model is `Qwen/Qwen2.5-1.5B` (small, ungated, GQA — realistic KV
layout). First run downloads ~3 GB of weights from HF.

## Smoke test first (on the Mac, before burning Spark time)

```
python collect_kv_dumps.py --model Qwen/Qwen2.5-0.5B \
    --run-id mac-smoke --reps 2 --max-tokens 128
```

This exercises every code path on CPU/MPS in a couple of minutes. If it
completes and prints the bit-stability summary, the script is good to go.

## Collection protocol on the Spark

```
# 1st invocation
python collect_kv_dumps.py --run-id spark-1

# 2nd invocation immediately after — cross-run diffs = restart noise (axis D)
python collect_kv_dumps.py --run-id spark-2

# with determinism flags — does it kill the noise? does anything error?
python collect_kv_dumps.py --run-id spark-det-1 --deterministic
python collect_kv_dumps.py --run-id spark-det-2 --deterministic

# if time allows: after a reboot, and/or with fp16
python collect_kv_dumps.py --run-id spark-postreboot-1
python collect_kv_dumps.py --run-id spark-fp16-1 --dtype float16
```

Then copy the whole output tree off the machine:

```
rsync -a kv_noise_data/ you@laptop:~/interlock-kv-noise/spark/
```

Budget: each run is a few dozen dumps at up to ~30 MB each (~28 KB/token
for the 1.5B model) — a few GB per run. Disk is the cheap resource here.

## Experiment axes (what each run contains)

| Axis | How | Question it answers |
|------|-----|---------------------|
| A repeat | N solo prefills, same process | within-process noise floor |
| B batch | target prompt batched with neighbors (size 2/4/8, position first/last) | does batching perturb the dump? (compute node doesn't control its batch-mates) |
| C chunked | prefill in two passes at 1/4, 1/2, 3/4 splits | restore-then-extend vs monolithic prefill — the exact honest storage path |
| D restart | separate `--run-id` invocations / reboots | cross-process, cross-boot stability |

## Analyzing collected runs

`analyze_runs.py` works from manifests alone (no torch, seconds on a
laptop against an rsync'd tree):

```
python analyze_runs.py kv_noise_data/          # discovers all run_* dirs
```

Part 1 reports within-run stability per (experiment, prompt), collapsing
same-hash dumps into one line per distinct outcome. Part 2 compares every
pair of runs on the same (model, dtype, attn) stack file-by-file on
sha256 — the restart/reboot/flags/cross-machine comparison. Numeric diffs
*between* runs (not just same/different) require loading the safetensors
pairs with `kv_diff_stats` from `collect_kv_dumps.py`.

## Reading the results

Every dump gets a SHA-256 over its raw tensor bytes plus a numeric diff
against a per-prompt reference (`manifest.jsonl`, also printed live):

- **`1/N unique` hashes in the summary** → bit-stable on that axis. If
  A, B, C are all bit-stable and hashes match across run-ids, an honest
  prover can predict its own dumps exactly and achieve Û ≈ 0 — the best
  possible outcome, letting the budget be set near zero.
- **Hashes differ**: read `ulp_max` / `ulp_le1_frac`, not `max_abs_diff`
  alone — floats are log-spaced, so "|diff| = 1.0" at a value of 140 is
  a single bf16 rounding step, not a large error. `ulp_le1_frac ≈ 1.0`
  means pure last-bit rounding noise (quantized serialization likely
  restores bit-stability); large `ulp_max` / `max_rel_diff` means real
  divergence and the prover needs the noise-model prediction path
  (paper's Q ∝ exp(−(v*−ℓ)²/s²) precedent), with the width σ fit from
  this data — and pays the corresponding surprisal per dump.
- **Expect divergence to be widespread, not localized.** The Mac smoke
  test already showed the mechanism: any change in kernel *shape*
  (a padded row, a chunked prefill) perturbs a few elements by 1 ULP,
  and layer-to-layer feedback amplifies those seeds until most elements
  differ (at small relative error) by the last layer. Same-shape reruns
  were bit-identical. Consequence: bit-exact recomputation requires
  reproducing the *exact kernel shapes* (same chunk splits, same
  padding) the original prefill used — or the prover's Q must absorb
  the amplified noise as paid surprisal. Measuring that trade-off is
  this dataset's job.

Cross-run and (later) cross-machine comparisons: diff the `sha256`
columns of two runs' `manifest.jsonl`, or load matching `.safetensors`
pairs and rerun `kv_diff_stats`.

## Results so far

**Mac MPS smoke (2026-07-16, Qwen2.5-0.5B, torch 2.13.0):** same-shape
reruns bit-identical, including across process restarts. Any kernel
*shape* change (a padded row in a batch, a chunked prefill) seeds a few
1-ULP diffs that layer-to-layer feedback amplifies until most elements
differ at small relative error. Chunk splits landing on kernel tile
boundaries (64-aligned, for 128-token prompts) happened to be bit-exact.

**DGX Spark GB10 full suite (2026-07-16, Qwen2.5-1.5B, torch
2.13.0+cu130, CUDA 13.0, 6 runs × 92 dumps):**

- **Zero stochastic noise.** Every cross-run pair — back-to-back
  processes, across a reboot, deterministic flags on vs off — is 92/92
  bit-identical, *including* the batched/chunked dumps that diverge from
  the solo reference. Every dump is a pure deterministic function of
  (tokens, execution shape). `--deterministic` changed nothing: the
  kernels this stack picks are already deterministic.
- **A (repeat):** bit-stable, all prompts, no flags needed.
- **B (batch):** all batched dumps diverge from the solo reference
  (frac_equal 0.17–0.25, ulp<=1 0.39–0.49) — unlike MPS, batching always
  perturbs here. But the structure is coarse and shape-only: target
  position never matters; batch contents never matter (bs4 vs bs8 have
  different neighbor rows, identical target dumps — attention masking
  isolates rows); short/medium/long collapse to one hash across
  bs2/4/8 despite different padded lengths, code splits into two
  (bs2 vs bs4/8 — likely a kernel tiling threshold near 696 tokens).
- **C (chunked):** every split diverges (frac_equal 0.09–0.79); no lucky
  tile-aligned bit-exact splits like on MPS. Same amplified-1-ULP
  character (huge ulp_max from near-zero sign flips, bulk of elements at
  small relative error).
- **fp16:** qualitatively identical to bf16.

**Design implication (prover recomputation, paper Option 1):** on a
fixed stack the problem collapses from "fit a noise distribution" to
"pin the execution shape". The prover records a few scalars per dump
(batch size, padded length, chunk splits) in its own private logs,
replays them at challenge time on its own hardware, and predicts the
dump bit-exactly — Û for an honest dump is exactly zero, at the cost of
logging a handful of integers. Nothing about the shapes, the hardware,
or the noise profile is revealed to the verifier; shape recording is a
prover-side optimization, not a protocol obligation. The incentive
structure enforces it for free: Q is the prover's own commitment under
the Gibbs bound, so a prover that *doesn't* pin shapes just pays
frac_equal ≈ 0.2 worth of surprisal per dump against a fixed budget —
there is no "our hardware is nondeterministic, grant us leeway" channel
to game. Noise-model prediction (σ from the frac_equal / ulp numbers
above) is the prover's fallback if its serving stack's shapes can't be
pinned; whether the budget can then still be met is the deployability
question this dataset quantifies.

**Open follow-ups** (both runnable remotely):

1. Direct test of shape-pinning: dump a target from a batch, recompute it
   batched with *dummy* rows of the same padded shape, check hash
   equality. (Inferred from bs4==bs8, not yet directly tested.) Under
   Option 1 the prover could replay the true batch-mates — their contents
   are measured inputs, admissible into the enclosure — but dummy-row
   equality means it only needs to log shapes, not batch composition.
2. Cross-hardware/cross-stack sweep — see below.

## Cross-hardware sweep (planned)

The scripts are already portable; a run is minutes on any single GPU.
Under prover recomputation the sweep has two customers, neither of which
is a verifier-side scoring model:

- **The verifier's budget policy.** The verifier's only calibration-shaped
  decision is where to set the unexplained-information budget. It needs
  public reference measurements — not measurements of any particular
  prover's hardware — showing what Û an honest, competently-engineered
  prover *can* achieve. The Spark result is the first existence proof
  that Û ≈ 0 per dump is reachable; the sweep asks whether legitimate
  hardware exists that genuinely can't reach it (e.g., stacks whose
  fastest kernels are atomics-based and truly stochastic), which is the
  budget-vs-hardware-exclusion policy tradeoff.
- **The prover's recompute engineering.** The paper's prototype sketch
  recomputes on a cheaper node than the serving cluster. Cross-stack
  divergence is then the *prover's* surprisal bill, not a soundness
  problem — the sweep maps which (serving stack → recompute stack) pairs
  allow cheap bit-exact recomputation and what mismatched pairs cost.

Frame it as a hierarchy of falsifiable claims, not per-GPU constants:

- **H1** fixed (hardware, stack, shape) → bit-deterministic
  (the Spark result; expected to hold broadly, must be confirmed per SKU)
- **H2** divergence depends only on execution shape, never on data
  content of batch-mates (under Option 1 this is convenience, not
  soundness — it lets the prover log a few integers instead of batch
  contents)
- **H3** two devices of the *same SKU + same stack* are bit-identical
  (determines whether the prover's recompute node can be a different
  physical unit of the serving SKU)
- **H4** cross-SKU / cross-arch / cross-torch-version agreement
  (expected false; measures what recomputing on non-matching hardware
  costs in surprisal — the fallback noise-model σ)

Suggested matrix, cheap on spot instances: A10G / L4 / A100 / H100 /
GH200 (arm, closest to GB10) / consumer 4090, × {torch 2.x, 2.y} ×
{sdpa, eager, flash-attn}. Same-SKU-two-instances covers H3. The
attention-backend and torch-version axes are software axes that likely
dominate the hardware axis. Later: multi-GPU tensor parallel (NCCL
reduction order) and a real serving stack (vLLM paged attention,
continuous batching) — likely the dominant real-world shape source.

Keep run-ids descriptive (`a100-torch213-sdpa-1`); meta.json records the
GPU/stack fingerprint, and `analyze_runs.py` flags cross-hardware pairs
automatically.

Data dirs (`kv_noise_data/`, venvs) should not be committed — dumps are
multi-GB. Keep them on the Mac / a drive; only this script and analysis
code go in the repo.
