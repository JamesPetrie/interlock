#!/usr/bin/env python3
"""Analyze KV-dump runs collected by collect_kv_dumps.py.

Works entirely from each run's manifest.jsonl and meta.json — no torch, no
tensor loading — so it runs in seconds on a laptop against an rsync'd data
tree. Two questions:

Part 1 (within-run): for each run, for each (experiment, prompt) group,
  how many distinct dump hashes are there, and how far do the divergent
  configs sit from the run's own solo reference (A rep0)? Dumps with the
  same hash are collapsed into one line listing the configs that produced
  them — the interesting structure is *which shapes* map to *which bits*.

Part 2 (cross-run, axis D): every pair of runs with the same (model,
  dtype, attn) is compared file-by-file on sha256. This is the restart /
  reboot / flags / machine comparison that can't be done inside a single
  collection run. Bit-identical here means the divergence seen in Part 1
  is *deterministic* — a function of execution shape, not noise.

Note the limit of manifest-only analysis: when two runs' hashes differ,
this script reports *that* they differ, not by how much. Numeric diff
stats between runs require loading the .safetensors pairs and calling
kv_diff_stats from collect_kv_dumps.py.

Usage:
    python analyze_runs.py kv_noise_data/            # discover all runs
    python analyze_runs.py dirA/run_x dirB/run_y     # explicit run dirs
"""
import argparse
import itertools
import json
import sys
from collections import defaultdict
from pathlib import Path


def discover_runs(paths):
    """Yield run directories (containing manifest.jsonl) under the given
    paths. A path that is itself a run dir is used directly."""
    for p in paths:
        p = Path(p)
        if (p / "manifest.jsonl").exists():
            yield p
        else:
            yield from sorted(
                m.parent for m in p.rglob("run_*/manifest.jsonl"))


def load_run(run_dir):
    rows = [json.loads(line)
            for line in (run_dir / "manifest.jsonl").read_text().splitlines()
            if line.strip()]
    meta = {}
    meta_path = run_dir / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
    return {"dir": run_dir, "name": run_dir.name, "meta": meta, "rows": rows}


def config_label(row):
    """The part of the filename that identifies the execution shape,
    without the rep counter (reps of one config are expected identical)."""
    parts = row["file"].replace(".safetensors", "").split("__")
    return "__".join(p for p in parts if not p.startswith("rep"))


def within_run(run):
    print(f"\n--- {run['name']} ({len(run['rows'])} dumps) "
          f"[{run['meta'].get('gpu') or run['meta'].get('device', '?')}, "
          f"{run['meta'].get('dtype', '?')}, torch {run['meta'].get('torch', '?')}, "
          f"det={run['meta'].get('deterministic', '?')}]")
    groups = defaultdict(list)
    for r in run["rows"]:
        groups[(r["experiment"], r["prompt_key"])].append(r)
    for (exp, prompt), g in sorted(groups.items()):
        by_hash = defaultdict(list)
        for r in g:
            by_hash[r["sha256"]].append(r)
        if len(by_hash) == 1 and g[0]["frac_equal"] == 1.0:
            print(f"  {exp} {prompt:8s}: {len(g)} dumps, BIT-STABLE")
            continue
        print(f"  {exp} {prompt:8s}: {len(g)} dumps, "
              f"{len(by_hash)} distinct hashes")
        for sha, rows in sorted(by_hash.items(), key=lambda kv: kv[1][0]["file"]):
            r = rows[0]
            configs = sorted({config_label(x) for x in rows})
            tag = ("== ref" if r["frac_equal"] == 1.0 else
                   f"frac_eq={r['frac_equal']:.4f} "
                   f"ulp<=1={r['ulp_le1_frac']:.4f} "
                   f"ulp_max={r['ulp_max']}")
            print(f"      {sha[:12]} ({len(rows)} dumps) {tag}")
            for c in configs:
                print(f"          {c}")


def stack_key(run):
    m = run["meta"]
    return (m.get("model"), m.get("dtype"), m.get("attn_implementation"))


def cross_run(runs):
    by_stack = defaultdict(list)
    for r in runs:
        by_stack[stack_key(r)].append(r)
    for key, group in sorted(by_stack.items(), key=str):
        if len(group) < 2:
            continue
        print(f"\nstack {key}:")
        for a, b in itertools.combinations(group, 2):
            ma = {r["file"]: r["sha256"] for r in a["rows"]}
            mb = {r["file"]: r["sha256"] for r in b["rows"]}
            common = sorted(set(ma) & set(mb))
            if not common:
                print(f"  {a['name']} vs {b['name']}: no common files")
                continue
            diff = [f for f in common if ma[f] != mb[f]]
            same_gpu = a["meta"].get("gpu") == b["meta"].get("gpu")
            note = "" if same_gpu else \
                f"  [CROSS-HW: {a['meta'].get('gpu')} vs {b['meta'].get('gpu')}]"
            print(f"  {a['name']} vs {b['name']}: "
                  f"{len(common) - len(diff)}/{len(common)} bit-identical{note}")
            if diff:
                by_exp = defaultdict(int)
                for f in diff:
                    by_exp[f.split("__")[0]] += 1
                detail = ", ".join(f"{e}: {n}" for e, n in sorted(by_exp.items()))
                print(f"      differing by experiment: {detail}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="+",
                    help="data root(s) or individual run directories")
    args = ap.parse_args()
    runs = [load_run(d) for d in discover_runs(args.paths)]
    if not runs:
        sys.exit("no runs found (looking for run_*/manifest.jsonl)")

    print("=" * 72)
    print("PART 1: WITHIN-RUN STABILITY (each run vs its own A-rep0 reference)")
    print("=" * 72)
    for run in runs:
        within_run(run)

    print()
    print("=" * 72)
    print("PART 2: CROSS-RUN — sha256 per file, all run pairs on same stack")
    print("=" * 72)
    cross_run(runs)


if __name__ == "__main__":
    main()
