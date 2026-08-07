#!/usr/bin/env python3
"""
collect_kv_dumps.py — honest KV-cache dump noise dataset collector.

WHY THIS SCRIPT EXISTS
======================

The storage-traffic-logging design scores a restored KV cache dump by
recomputing it. Under prover recomputation (Option 1 in
paper/UnexplainedInformation.pdf; see also docs/security-architecture.md
§6), the prover re-runs prefill on the claimed context on its own
hardware inside a verifier-controlled enclosure, commits a prediction Q
of the dump, and the interlock charges the surprisal −log2 Q against a
fixed unexplained-information budget. An honest dump is a pure function
of (context tokens, model weights), so in an ideal world the prover's
recomputation reproduces it bit-for-bit and the unexplained information
U is zero.

The real world is not ideal: floating-point addition is not associative,
GPUs sum in whatever order the kernel's thread scheduling produces, and
kernel *selection* itself can depend on batch size, sequence length, and
library version. So two honest prefills of the same context can produce
KV tensors that differ in low-order bits — or worse. Every bit of honest
divergence is surprisal an honest prover is forced to pay per dump, and
therefore a floor on how low the verifier can set the budget — and any
budget above that floor is bandwidth a dishonest prover could spend on
smuggled information instead. (Q is the prover's own commitment under
the Gibbs bound, so there is no leeway to negotiate: claimed
nondeterminism just shows up as the prover's own paid surprisal.)

This script's job is to MEASURE that noise on the actual serving hardware
(the DGX Spark). It deliberately does data collection only — no analysis,
no interlock integration. Dumps are cheap to store and expensive to regret
not having; analysis happens offline (on a laptop) from the saved tensors.

BACKGROUND: WHAT PREFILL AND THE KV CACHE ACTUALLY ARE
======================================================

A decoder-only transformer generates text in two phases:

  1. PREFILL — the whole prompt (say 1000 tokens) is pushed through the
     model in ONE parallel forward pass. At every layer, every token
     computes an attention Key vector and a Value vector. These K/V
     vectors are what later tokens attend back to, so they're kept.

  2. DECODE — new tokens are generated one at a time. Each new token
     only needs to compute ITS OWN q/k/v and attend to the STORED keys
     and values of everything before it. Without the cache you'd have to
     re-run the entire prompt through the model for every generated
     token; with it, decode is O(1) forward passes per token.

The stored K/V vectors are the "KV cache". Concretely, for a model with
L layers, it is L pairs of tensors, each shaped:

     [batch, n_kv_heads, seq_len, head_dim]

  - batch      : how many sequences were prefilled together
  - n_kv_heads : number of key/value attention heads. Often SMALLER than
                 the number of query heads ("grouped-query attention",
                 GQA) — e.g. Qwen2.5-1.5B has 12 query heads but only
                 2 KV heads. GQA exists precisely to shrink the KV cache.
  - seq_len    : one K and one V vector per prompt token
  - head_dim   : vector width per head (typically 64–128)

Size intuition: bytes/token = 2 (K and V) x L x n_kv_heads x head_dim x
bytes/elem. For Qwen2.5-1.5B in bf16 that's 2*28*2*128*2 = 28 KB per
token, so a 1000-token prompt yields a ~28 MB cache. THIS is the object
production serving stacks dump to disk ("prefix caching") so a later
request sharing the same prompt prefix can skip prefill — and therefore
this is exactly the traffic class our interlock must log and score.

One misconception to head off: none of this involves randomness. Prefill
is a deterministic forward pass — there is no sampling, no RNG, and seeds
are irrelevant. Any run-to-run difference we observe comes purely from
floating-point reduction order and kernel selection. That's why the
experiment axes below vary *execution conditions*, not seeds.

EXPERIMENT AXES
===============

  A. repeat   — same prompt, batch of 1, N times in the same process.
                The within-process noise floor. If even this isn't
                bit-stable, everything downstream must be noise-tolerant.

  B. batch    — the same target prompt prefilled alongside different
                "neighbor" prompts, at different batch sizes and batch
                positions. Batching changes matmul shapes, which changes
                kernel/tile selection, which changes summation order —
                a classic source of divergence. In production the compute
                node does NOT control who it's batched with, so any
                batch-induced noise is part of the honest baseline.

  C. chunked  — prefill the prompt in two passes (first s tokens, then
                the rest with the first chunk's cache). This models both
                vLLM-style chunked prefill AND the restore-then-extend
                path (restore a dumped prefix cache, prefill only the
                suffix) — the exact honest behavior our storage design
                legalizes via the context-prefix rule. If chunked prefill
                diverges from monolithic prefill, the prover's
                recomputation has to replay the same chunking (or eat
                the surprisal).

  D. restart  — process/driver/reboot boundaries. Not a mode inside this
                script: just run the whole script again with a different
                --run-id (and after a reboot, ideally). Comparisons across
                run dirs happen offline; the per-dump SHA-256 hashes make
                them trivial.

The ultimate axis — DIFFERENT hardware (serving node vs whatever the
prover's recompute enclosure runs) — is future work: run this same
script on the other machine and diff manifests. Any divergence there is
the prover's surprisal cost for recomputing on a non-matching stack.

OUTPUT LAYOUT
=============

  <outdir>/run_<run-id>/
      meta.json        environment fingerprint (versions, GPU, flags)
      prompts.json     the exact token ids prefilled (analysis needs them)
      manifest.jsonl   one line per dump: file, sha256, config, quick diffs
      dumps/*.safetensors   the raw KV tensors, one file per prefill

Tensors are saved RAW, in the compute dtype, with no quantization.
Quantization (INT8/FP8 serialization candidates) is a deterministic
function of the raw tensor, so it is applied OFFLINE during analysis —
storing raw keeps this script simple and preserves every option.

QUICK IN-SESSION FEEDBACK
=========================

You should not have to wait for offline analysis to know whether the
hardware is wildly non-deterministic. As it runs, the script:
  - hashes every dump (identical hashes == bit-identical dumps), and
  - diffs every dump against a per-prompt reference (experiment A, rep 0),
    printing max |diff| and the fraction of exactly-equal elements.
A final summary table groups dumps by config and reports unique-hash
counts. "1 unique hash" for a group means bit-stable under that axis.

USAGE
=====

  # smoke test on a laptop (tiny model, CPU/MPS):
  python collect_kv_dumps.py --model Qwen/Qwen2.5-0.5B --run-id mac-smoke \
      --reps 2 --max-tokens 128

  # real collection on the Spark (run at least twice for axis D):
  python collect_kv_dumps.py --run-id spark-1
  python collect_kv_dumps.py --run-id spark-2

Dependencies: torch, transformers, safetensors  (pip install torch
transformers safetensors). No interlock/repo code is imported.
"""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

# NOTE: torch/transformers are imported inside main(), not here. Reason:
# some determinism knobs (CUBLAS_WORKSPACE_CONFIG) must be set in the
# environment before the CUDA libraries initialize, and we want argparse
# to run (e.g. --help) without requiring the heavy deps installed.


# ---------------------------------------------------------------------------
# Built-in prompts
# ---------------------------------------------------------------------------
# We want a spread of sequence lengths because kernel selection is often
# length-dependent (different tile sizes / split-K strategies kick in at
# different sizes). Text content itself shouldn't matter much, but we
# include prose and code-ish text since tokenization density differs.
# These strings are deterministic (no timestamps, no randomness) so every
# run of the script prefills byte-identical token sequences.

_PARA = (
    "The verifier interlock sits inline on the only network path of a "
    "quarantined compute node and commits every canonical packet into a "
    "hash chain, emitting HMAC-signed certificates once per epoch. "
    "Because packets are self-locating, the unordered set of committed "
    "records reconstructs every per-bucket digest, which makes unbiased "
    "byte sampling sound. "
)

_CODE = (
    "def fold(bucket, record):\n"
    "    # per-bucket running SHA-256 over canonical records\n"
    "    h = hashlib.sha256(bucket.state)\n"
    "    h.update(record.pld_len.to_bytes(4, 'big'))\n"
    "    h.update(record.digest)\n"
    "    return h.digest()\n\n"
)

DEFAULT_PROMPTS = {
    # ~40 tokens: short prompt, likely a single kernel tile.
    "short": "The quick brown fox jumps over the lazy dog. " * 4,
    # ~300 tokens: medium prose.
    "medium": _PARA * 5,
    # ~1100 tokens: long prose — the realistic prefix-caching regime.
    "long": _PARA * 18,
    # ~700 tokens of code-like text (different token distribution).
    "code": _CODE * 12,
}


# ---------------------------------------------------------------------------
# Environment fingerprint
# ---------------------------------------------------------------------------
def collect_metadata(args, torch, transformers, device):
    """Record everything that could plausibly explain a numeric difference
    between two runs. When (not if) two dumps disagree, attribution starts
    here — 'same everything except driver version' is a finding."""
    meta = {
        "run_id": args.run_id,
        "wall_time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "script_sha256": hashlib.sha256(
            Path(__file__).read_bytes()
        ).hexdigest(),
        "argv": sys.argv[1:],
        "model": args.model,
        "dtype": args.dtype,
        "attn_implementation": args.attn_implementation,
        "deterministic": args.deterministic,
        "device": str(device),
        "python": sys.version,
        "platform": platform.platform(),
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "cuda_available": torch.cuda.is_available(),
    }
    if torch.cuda.is_available():
        meta.update(
            {
                "cuda": torch.version.cuda,
                "cudnn": torch.backends.cudnn.version(),
                "gpu": torch.cuda.get_device_name(0),
                # TF32 silently replaces fp32 matmuls with a 19-bit-mantissa
                # tensor-core mode. We record it because it's a common hidden
                # source of "same code, different numbers" across machines.
                "allow_tf32_matmul": torch.backends.cuda.matmul.allow_tf32,
                "allow_tf32_cudnn": torch.backends.cudnn.allow_tf32,
                "cublas_workspace_config": os.environ.get(
                    "CUBLAS_WORKSPACE_CONFIG"
                ),
            }
        )
        # Driver version comes from nvidia-smi, not torch.
        try:
            smi = subprocess.run(
                ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=10,
            )
            meta["nvidia_driver"] = smi.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            meta["nvidia_driver"] = "unavailable"
    return meta


# ---------------------------------------------------------------------------
# KV-cache extraction
# ---------------------------------------------------------------------------
def extract_kv(past_key_values, torch):
    """Normalize HuggingFace's cache object into a plain list of
    (K, V) CPU tensor pairs, one per layer.

    transformers has changed its cache container several times
    (legacy tuple -> DynamicCache with .key_cache/.value_cache lists ->
    Cache with .layers[i].keys/values), so we probe in order of
    preference rather than pinning one API. Tensors come out shaped
    [batch, n_kv_heads, seq_len, head_dim] in all of them.
    """
    if hasattr(past_key_values, "to_legacy_cache"):
        layers = past_key_values.to_legacy_cache()
    elif hasattr(past_key_values, "layers"):
        layers = [(l.keys, l.values) for l in past_key_values.layers]
    elif hasattr(past_key_values, "key_cache"):
        layers = list(
            zip(past_key_values.key_cache, past_key_values.value_cache)
        )
    else:
        layers = past_key_values  # legacy tuple-of-tuples
    # .detach() drops autograd bookkeeping; .cpu() moves off the GPU so we
    # can hash/serialize. We do NOT change dtype — the dump must preserve
    # the exact bits the model produced.
    return [(k.detach().cpu(), v.detach().cpu()) for (k, v) in layers]


def kv_to_tensor_dict(kv_layers, batch_index, seq_len, torch):
    """Slice out one sequence's cache from a (possibly batched, possibly
    right-padded) KV cache and flatten it into a {name: tensor} dict for
    safetensors.

    Padding note: we tokenize with padding_side='right', so for a batch
    row whose real prompt is L tokens long, positions [0, L) are real and
    [L, max_len) are padding garbage. Right padding is chosen deliberately:
    it keeps the target prompt's absolute positions identical whether it
    runs solo or in a batch, so experiment B isolates the *batching* effect
    from any position effect. We slice off the padding here — padded
    positions are masked out of attention and their K/V never influence
    real tokens, so they are not part of the honest dump.
    """
    out = {}
    for i, (k, v) in enumerate(kv_layers):
        out[f"layer{i:02d}.k"] = k[batch_index, :, :seq_len, :].contiguous()
        out[f"layer{i:02d}.v"] = v[batch_index, :, :seq_len, :].contiguous()
    return out


def kv_sha256(tensors, torch):
    """SHA-256 over the raw bytes of every tensor, in sorted name order.

    Two dumps with equal hashes are bit-identical — the strongest possible
    'no noise' result, checkable at a glance and comparable across
    machines by just diffing manifest files. (This mirrors how the
    interlock itself treats content: commit to bytes, compare digests.)
    """
    h = hashlib.sha256()
    for name in sorted(tensors):
        t = tensors[name].contiguous().flatten()
        h.update(name.encode())
        # .view(torch.uint8) reinterprets the tensor's memory as raw bytes
        # without converting values (works for bf16, which numpy can't
        # represent natively).
        h.update(t.view(torch.uint8).numpy().tobytes())
    return h.hexdigest()


def _float_order_key(t, torch):
    """Map a float tensor's raw bits to integers whose DIFFERENCE is the
    ULP (units-in-last-place) distance between the floats.

    Why ULP and not absolute difference: floats are log-spaced. In bf16,
    near a value of 140 one ULP is 1.0, while near 0.01 one ULP is
    ~0.00006 — so an absolute diff of "1.0" can be the smallest possible
    rounding wobble, not a large error. ULP distance is the honest
    "how many representable values apart" metric, and it's also exactly
    the quantity a quantized-serialization decision needs (a dump that's
    everywhere within k ULPs survives any quantization coarser than
    k low bits).

    The mapping is the standard IEEE-754 total-order trick: positive
    floats map to bits + 2^(w-1), negative floats to 2^w - bits, which
    makes the integer line monotonic in float value (and ±0 coincide).
    """
    if t.dtype in (torch.bfloat16, torch.float16):
        bits = t.contiguous().view(torch.int16).to(torch.int32) & 0xFFFF
        half, full = 1 << 15, 1 << 16
    elif t.dtype == torch.float32:
        bits = t.contiguous().view(torch.int32).to(torch.int64) & 0xFFFFFFFF
        half, full = 1 << 31, 1 << 32
    else:
        raise ValueError(f"unsupported dtype {t.dtype}")
    return torch.where(bits < half, bits + half, full - bits)


def kv_diff_stats(a, b, torch):
    """Cheap numeric comparison between two dumps of the SAME prompt.

    Reported per dump against a fixed reference (experiment A, rep 0):
      frac_equal   — fraction of elements that are EXACTLY (bitwise) equal
      max_abs_diff — worst-case element difference (in float32). Beware:
                     misleading on its own, since 1.0 at magnitude 140 is
                     a single bf16 rounding step (see _float_order_key).
      max_rel_diff — worst-case |diff| / (|ref| + 1e-6)
      ulp_max      — worst-case ULP distance
      ulp_le1_frac — fraction of elements within 1 ULP of the reference.
                     ~1.0 here with frac_equal < 1 means pure last-bit
                     rounding noise; large ulp_max means real divergence.
    """
    max_abs = 0.0
    max_rel = 0.0
    ulp_max = 0
    n_equal = 0
    n_ulp_le1 = 0
    n_total = 0
    for name in sorted(a):
        ta, tb = a[name], b[name]
        if ta.shape != tb.shape:  # shouldn't happen for same prompt
            return {"max_abs_diff": None, "frac_equal": None,
                    "note": "shape mismatch"}
        d = (ta.float() - tb.float()).abs()
        max_abs = max(max_abs, d.max().item())
        max_rel = max(max_rel, (d / (ta.float().abs() + 1e-6)).max().item())
        ulp = (_float_order_key(ta, torch)
               - _float_order_key(tb, torch)).abs()
        ulp_max = max(ulp_max, ulp.max().item())
        n_ulp_le1 += (ulp <= 1).sum().item()
        n_equal += (ta == tb).sum().item()
        n_total += ta.numel()
    return {"max_abs_diff": max_abs, "max_rel_diff": max_rel,
            "ulp_max": ulp_max, "ulp_le1_frac": n_ulp_le1 / n_total,
            "frac_equal": n_equal / n_total}


# ---------------------------------------------------------------------------
# Prefill runners
# ---------------------------------------------------------------------------
def prefill(model, input_ids, attention_mask, torch):
    """One prefill = one forward pass with use_cache=True.

    We ignore the logits entirely; the artifact of interest is
    past_key_values. inference_mode() disables autograd (faster, and
    guarantees no grad-related buffers leak into the dump).
    """
    with torch.inference_mode():
        out = model(
            input_ids=input_ids,
            attention_mask=attention_mask,
            use_cache=True,
        )
    return out.past_key_values


def prefill_chunked(model, input_ids, split, torch):
    """Two-pass prefill: tokens [0, split) first, then [split, end) with
    the first pass's cache carried in via past_key_values.

    This is mechanically identical to what happens on a cache RESTORE:
    load a dumped prefix cache, then prefill only the new suffix. The
    attention math is equivalent to monolithic prefill — the suffix
    tokens attend to the stored prefix K/V exactly as they would to
    freshly computed ones — but the kernel invocations are different
    (two smaller calls instead of one big one), so the floating-point
    reduction orders differ and the bits may not match.
    """
    n = input_ids.shape[1]
    with torch.inference_mode():
        out1 = model(
            input_ids=input_ids[:, :split],
            attention_mask=torch.ones(1, split, dtype=torch.long,
                                      device=input_ids.device),
            use_cache=True,
        )
        # Second pass: the attention mask must cover prefix + suffix so
        # the suffix tokens can see the cached prefix.
        out2 = model(
            input_ids=input_ids[:, split:],
            attention_mask=torch.ones(1, n, dtype=torch.long,
                                      device=input_ids.device),
            past_key_values=out1.past_key_values,
            use_cache=True,
        )
    # out2's cache now holds the FULL sequence (prefix entries were
    # appended to in place / carried through), so it's directly
    # comparable to a monolithic prefill of the whole prompt.
    return out2.past_key_values


# ---------------------------------------------------------------------------
# Dump recording
# ---------------------------------------------------------------------------
class Recorder:
    """Saves dumps, hashes them, diffs them against the per-prompt
    reference, and appends manifest lines. One instance per run."""

    def __init__(self, run_dir, torch, save_file):
        self.dump_dir = run_dir / "dumps"
        self.dump_dir.mkdir(parents=True, exist_ok=True)
        self.manifest = open(run_dir / "manifest.jsonl", "a")
        self.torch = torch
        self.save_file = save_file
        self.references = {}   # prompt_key -> tensor dict (A rep 0)
        self.rows = []

    def record(self, tensors, *, experiment, prompt_key, rep, **config):
        digest = kv_sha256(tensors, self.torch)
        fname = f"{experiment}__{prompt_key}__rep{rep}"
        for k, v in sorted(config.items()):
            if v is not None:
                fname += f"__{k}-{v}"
        fname += ".safetensors"
        # safetensors metadata values must be strings.
        self.save_file(
            tensors, str(self.dump_dir / fname),
            metadata={"sha256": digest, "experiment": experiment,
                      "prompt_key": prompt_key, "rep": str(rep)},
        )

        # The very first A-dump for each prompt becomes the reference all
        # later dumps of that prompt are numerically diffed against.
        ref = self.references.get(prompt_key)
        if ref is None and experiment == "A":
            self.references[prompt_key] = tensors
            diff = {"max_abs_diff": 0.0, "max_rel_diff": 0.0,
                    "ulp_max": 0, "ulp_le1_frac": 1.0, "frac_equal": 1.0}
        elif ref is not None:
            diff = kv_diff_stats(ref, tensors, self.torch)
        else:
            diff = {"max_abs_diff": None, "frac_equal": None}

        row = {"file": fname, "sha256": digest, "experiment": experiment,
               "prompt_key": prompt_key, "rep": rep, **config, **diff}
        self.manifest.write(json.dumps(row) + "\n")
        self.manifest.flush()  # survive a crash mid-run
        self.rows.append(row)

        stable = "bit-identical to ref" if diff.get("frac_equal") == 1.0 \
            else (f"max|d|={diff['max_abs_diff']:.3e} "
                  f"maxrel={diff['max_rel_diff']:.1e} "
                  f"ulp_max={diff['ulp_max']} "
                  f"ulp<=1={diff['ulp_le1_frac']:.4f} "
                  f"equal={diff['frac_equal']:.4f}"
                  if diff.get("frac_equal") is not None else "no ref")
        print(f"  [{experiment}] {prompt_key} rep{rep} "
              f"{config or ''} sha={digest[:12]}  {stable}")

    def summary(self):
        """Group dumps by (experiment, prompt, config) and count unique
        hashes — the one-glance bit-stability report."""
        groups = {}
        for r in self.rows:
            cfg = tuple(sorted(
                (k, v) for k, v in r.items()
                if k not in ("file", "sha256", "rep",
                             "max_abs_diff", "frac_equal")
            ))
            groups.setdefault(cfg, []).append(r["sha256"])
        print("\n=== bit-stability summary (this run only) ===")
        print("unique_hashes == 1 within a group => bit-stable on that axis")
        for cfg, hashes in groups.items():
            label = " ".join(f"{k}={v}" for k, v in cfg)
            print(f"  {len(set(hashes))}/{len(hashes)} unique  {label}")
        print("\nCross-run (restart axis D) and cross-machine comparisons: "
              "diff the sha256 columns of the manifest.jsonl files offline.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def build_argparser():
    p = argparse.ArgumentParser(
        description="Collect honest KV-cache dump noise dataset "
                    "(see module docstring)."
    )
    p.add_argument("--model", default="Qwen/Qwen2.5-1.5B",
                   help="HF model id. Default is small, ungated, and GQA "
                        "(realistic KV layout). Use Qwen/Qwen2.5-0.5B for "
                        "laptop smoke tests.")
    p.add_argument("--run-id", required=True,
                   help="Names the output dir. Use distinct ids per "
                        "invocation (spark-1, spark-2, ...) — cross-id "
                        "diffs ARE experiment D (restart noise).")
    p.add_argument("--outdir", default="kv_noise_data",
                   help="Base output directory.")
    p.add_argument("--dtype", default="bfloat16",
                   choices=["bfloat16", "float16", "float32"],
                   help="Compute/storage dtype. bf16 is the serving "
                        "default on modern NVIDIA hardware.")
    p.add_argument("--attn-implementation", default="sdpa",
                   choices=["sdpa", "eager", "flash_attention_2"],
                   help="Attention kernel family. Different families "
                        "WILL differ numerically; the question this "
                        "script answers is noise WITHIN one family. "
                        "sdpa is torch's default fused path.")
    p.add_argument("--deterministic", action="store_true",
                   help="Ask torch for deterministic algorithms "
                        "(torch.use_deterministic_algorithms + cuBLAS "
                        "workspace pin). Run with AND without this flag: "
                        "if it kills the noise, that's a deployment "
                        "requirement finding; if unsupported ops error "
                        "out, that's a finding too.")
    p.add_argument("--reps", type=int, default=5,
                   help="Repetitions for experiment A (same-process "
                        "repeat). B and C use 2 reps each — A already "
                        "characterizes within-config repeatability, so "
                        "extra configs beat extra reps there.")
    p.add_argument("--max-tokens", type=int, default=None,
                   help="Truncate every prompt to this many tokens "
                        "(smoke tests / memory limits).")
    p.add_argument("--prompts-file", default=None,
                   help="Optional JSON file {key: text} to replace the "
                        "built-in prompts.")
    p.add_argument("--experiments", default="A,B,C",
                   help="Comma-separated subset of A,B,C to run.")
    p.add_argument("--device", default="auto",
                   choices=["auto", "cuda", "mps", "cpu"])
    return p


def main():
    args = build_argparser().parse_args()

    # cuBLAS reads this env var at initialization time, so it must be set
    # before the first CUDA op. ":4096:8" pins the workspace size, which
    # removes one source of run-to-run reduction-order variation and is
    # REQUIRED for torch.use_deterministic_algorithms on CUDA.
    if args.deterministic:
        os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")

    import torch
    import transformers
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from safetensors.torch import save_file

    if args.deterministic:
        torch.use_deterministic_algorithms(True)

    if args.device == "auto":
        device = ("cuda" if torch.cuda.is_available()
                  else "mps" if torch.backends.mps.is_available()
                  else "cpu")
    else:
        device = args.device
    dtype = getattr(torch, args.dtype)

    run_dir = Path(args.outdir) / f"run_{args.run_id}"
    if (run_dir / "meta.json").exists():
        sys.exit(f"run dir {run_dir} already exists — pick a new --run-id "
                 "(each id is one 'restart' sample; don't mix them)")
    run_dir.mkdir(parents=True)

    print(f"loading {args.model} ({args.dtype}, "
          f"{args.attn_implementation}) on {device} ...")
    tokenizer = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForCausalLM.from_pretrained(
        args.model, torch_dtype=dtype,
        attn_implementation=args.attn_implementation,
    ).to(device)
    model.eval()  # inference mode: disables dropout etc. (belt & braces —
    #               prefill has no sampling, but eval() makes it explicit)

    # Some tokenizers (incl. Qwen's) define no pad token because training
    # never needed one; batching does. Reusing EOS as pad is standard and
    # harmless here since padded positions are masked out of attention.
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"  # see kv_to_tensor_dict() for why

    meta = collect_metadata(args, torch, transformers, device)
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    # ---- tokenize prompts once, save the exact ids -----------------------
    # Offline analysis (and any future recomputation on another machine)
    # must prefill the SAME token ids, not re-tokenize text — tokenizer
    # versions drift. So token ids are part of the dataset.
    if args.prompts_file:
        prompts = json.loads(Path(args.prompts_file).read_text())
    else:
        prompts = DEFAULT_PROMPTS
    tokenized = {}
    for key, text in prompts.items():
        ids = tokenizer(text, return_tensors="pt").input_ids[0]
        if args.max_tokens:
            ids = ids[: args.max_tokens]
        tokenized[key] = ids
    (run_dir / "prompts.json").write_text(json.dumps(
        {k: {"text": prompts[k], "n_tokens": len(v), "ids": v.tolist()}
         for k, v in tokenized.items()}, indent=2))
    seen_ids = {}
    for k, v in tokenized.items():
        dup = seen_ids.get(tuple(v.tolist()))
        # Identical token sequences (easy to hit via --max-tokens
        # truncating two repetitions of the same text) legitimately
        # produce identical dumps — flag it so equal hashes across
        # prompt keys aren't misread as a cross-prompt collision.
        note = f"  (same tokens as '{dup}'!)" if dup else ""
        seen_ids.setdefault(tuple(v.tolist()), k)
        print(f"  prompt '{k}': {len(v)} tokens{note}")

    rec = Recorder(run_dir, torch, save_file)
    experiments = set(args.experiments.split(","))

    # ---- experiment A: same-process repeats ------------------------------
    # NOTE: A must run first (even if you only asked for B/C we force one
    # A rep) because its rep-0 dump is the numeric reference everything
    # else is diffed against.
    reps_a = args.reps if "A" in experiments else 1
    print(f"\n--- experiment A: {reps_a} solo repeats per prompt ---")
    for key, ids in tokenized.items():
        batch = ids.unsqueeze(0).to(device)          # [1, seq_len]
        mask = torch.ones_like(batch)
        for rep in range(reps_a):
            kv = extract_kv(prefill(model, batch, mask, torch), torch)
            tensors = kv_to_tensor_dict(kv, 0, len(ids), torch)
            rec.record(tensors, experiment="A", prompt_key=key, rep=rep)

    # ---- experiment B: batch composition ---------------------------------
    if "B" in experiments:
        print("\n--- experiment B: batched prefill "
              "(varying size and target position) ---")
        prompt_keys = list(tokenized)
        for key, ids in tokenized.items():
            # Neighbor pool: the other prompts, cycled to fill the batch.
            # Content of neighbors is arbitrary — what matters is that the
            # batch SHAPE and the target's row position change.
            others = [k for k in prompt_keys if k != key] or [key]
            for batch_size in (2, 4, 8):
                for target_index in (0, batch_size - 1):
                    neighbor_keys = [others[i % len(others)]
                                     for i in range(batch_size - 1)]
                    batch_keys = list(neighbor_keys)
                    batch_keys.insert(target_index, key)
                    enc = tokenizer.pad(
                        {"input_ids": [tokenized[k].tolist()
                                       for k in batch_keys]},
                        return_tensors="pt",
                    )
                    for rep in range(2):
                        kv = extract_kv(
                            prefill(model,
                                    enc.input_ids.to(device),
                                    enc.attention_mask.to(device),
                                    torch),
                            torch)
                        tensors = kv_to_tensor_dict(
                            kv, target_index, len(ids), torch)
                        rec.record(tensors, experiment="B",
                                   prompt_key=key, rep=rep,
                                   batch_size=batch_size,
                                   target_index=target_index)

    # ---- experiment C: chunked prefill / restore-then-extend -------------
    if "C" in experiments:
        print("\n--- experiment C: chunked prefill (split at 1/4, 1/2, 3/4) ---")
        for key, ids in tokenized.items():
            n = len(ids)
            batch = ids.unsqueeze(0).to(device)
            for frac_name, split in (("q1", n // 4), ("q2", n // 2),
                                     ("q3", 3 * n // 4)):
                if split < 1 or split >= n:
                    continue  # prompt too short to split here
                for rep in range(2):
                    kv = extract_kv(
                        prefill_chunked(model, batch, split, torch), torch)
                    tensors = kv_to_tensor_dict(kv, 0, n, torch)
                    rec.record(tensors, experiment="C",
                               prompt_key=key, rep=rep,
                               split=f"{frac_name}-{split}")

    rec.summary()
    print(f"\ndone. data in {run_dir}/ — copy the whole run dir off the "
          "Spark; analysis happens offline.")


if __name__ == "__main__":
    main()
