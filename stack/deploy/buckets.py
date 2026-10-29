"""Per-bucket occupancy and landing from the interlock's trace (ILSTACK_TRACE=1):
packets per bucket, where the first and the last slot of a bucket landed, and drops."""
import re
import sys
from collections import defaultdict

path = sys.argv[1]
buckets = defaultdict(list)                       # (dir, bucket) -> [(slot, delta_us)]
drops = defaultdict(list)                         # (dir, reason) -> [(bucket, slot, now)]
for line in open(path):
    m = re.match(r"TRACE check (\d+) [\d.]+ (\d+)", line)
    if m:
        v = int(m[1]); d, b, s = v >> 48, (v >> 16) & 0xFFFFFFFF, v & 0xFFFF
        buckets[(d, b)].append((s, int(m[2])))
    m = re.search(r"drop bucket (inbound|outbound): Id\(dir=\d+, bucket=(\d+), slot=(\d+)\) (late|early), now (\d+)", line)
    if m:
        drops[(m[1], m[4])].append((int(m[2]), int(m[3]), int(m[5])))


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None


for d, name in ((0, "inbound"), (0xFFFF, "outbound")):
    bs = {k: v for k, v in buckets.items() if k[0] == d}
    if not bs:
        continue
    sizes = [len(v) for v in bs.values()]
    print(f"{name}: {sum(sizes)} packets in {len(bs)} buckets; packets per bucket p50={pct(sizes, .5)} p90={pct(sizes, .9)} max={max(sizes)}")
    full = {k: v for k, v in bs.items() if len(v) >= 8}
    if full:
        first = [min(v)[1] for v in full.values()]
        last = [max(v)[1] for v in full.values()]
        span = [max(v)[1] - min(v)[1] for v in full.values()]
        print(f"  buckets with >= 8 packets: {len(full)}; first slot landed p50={pct(first, .5)} max={max(first)} us; "
              f"last slot landed p50={pct(last, .5)} p90={pct(last, .9)} max={max(last)} us; "
              f"first-to-last span p50={pct(span, .5)} max={max(span)} us")
        per_pkt = [sp / (len(v) - 1) for v, sp in zip(full.values(), span) if len(v) > 1]
        print(f"  time per packet within a bucket p50={pct(per_pkt, .5):.0f} max={max(per_pkt):.0f} us")
for (name, reason), v in sorted(drops.items()):
    slots = [s for _, s, _ in v]
    print(f"drops {name} {reason}: {len(v)}; slots involved min={min(slots)} max={max(slots)}")
if not drops:
    print("drops: none")
