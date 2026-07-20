"""Bucket-timing calibration probe against the flashed interlock build.

Plays the role the prod_canon_proc doc assigns the sender: lock to the tick
cadence from sync packets (ID=1), aim probes at a chosen intra-bucket offset,
and use the FIRST_ARR feedback (FPGA 80 MHz timer at ingest, 12.5 ns units) to
close the loop and measure landing error. One probe per bucket, RSP format,
sent on the server-facing NIC; the response-direction sync arrives on the same
NIC.

Loop discipline: probes are scheduled off each sync arrival (the tick edge) at
offset_frac into the bucket that edge opens, so the send window sits mid-bucket
and the process is always parked in recv() when the next sync lands — sync
host timestamps stay honest.

usage: calib_probe.py <iface> <n_buckets> [offset_frac] [--tuned]
"""
import socket, struct, sys, time, os, json

IFACE = sys.argv[1]
NBKT  = int(sys.argv[2])
FRAC  = float(sys.argv[3]) if len(sys.argv) > 3 and not sys.argv[3].startswith("--") else 0.5
TUNED = "--tuned" in sys.argv

FCLK_HZ   = 80_000_000           # fabric clock: FIRST_ARR/timer units
SYNC_ID   = 1
HDR_BYTES = 64
NOARR     = 0xFFFFFFFF           # first_arr when bucket saw no accepted packet

if TUNED:
    try:
        os.sched_setaffinity(0, {os.cpu_count() - 1})
        os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(50))
        print("# tuned: SCHED_FIFO 50, pinned to cpu%d" % (os.cpu_count() - 1))
    except OSError as e:
        print("# WARNING tuned mode unavailable: %s" % e)

rx = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(0x0003))
rx.bind((IFACE, 0))
# promiscuous: station MACs are not this NIC's MAC
rx.setsockopt(263, 1, struct.pack("iHH8s", socket.if_nametoindex(IFACE), 1, 0, b""))
tx = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
tx.bind((IFACE, 0))

def rsp_probe(bucket, ident):
    hdr = struct.pack("!IIQ", 0, bucket & 0xFFFFFFFF, ident) + b"\x00" * 48
    eth = b"\xde\xad\xbe\xef\x00\x02" + b"\xde\xad\xbe\xef\x00\x01" + struct.pack("!H", HDR_BYTES)
    return eth + hdr

def parse_sync(fr):
    if len(fr) < 14 + HDR_BYTES or fr[12:14] != struct.pack("!H", HDR_BYTES):
        return None
    p = fr[14:14 + HDR_BYTES]
    first_arr, bucket = struct.unpack("!II", p[0:8])
    if int.from_bytes(p[8:16], "big") != SYNC_ID:
        return None
    return bucket, first_arr

# --- initial lock: least-squares fit of edge time vs bucket ---------------
# Sync ARRIVAL times are noisy (r8127 interrupt moderation, no ethtool -C
# support), so they are used only for the initial fit and bucket numbering;
# steady-state phase/rate come from FIRST_ARR (FPGA-clock-measured, immune to
# the return path) via a PI servo.
rx.settimeout(1.0)
obs = []                          # (bucket_closed, host_ns)
t_lock0 = time.monotonic_ns()
while len(obs) < 30:
    if time.monotonic_ns() - t_lock0 > 40e9:
        print("FAIL: no sync stream on %s" % IFACE); sys.exit(1)
    try:
        fr = rx.recv(2048)
    except socket.timeout:
        continue
    t = time.monotonic_ns()
    s = parse_sync(fr)
    if s:
        obs.append((s[0], t))
b_ref = obs[-1][0]
xs = [b - b_ref for b, _ in obs]
ys = [t - obs[-1][1] for _, t in obs]
n = len(obs)
sx, sy = sum(xs), sum(ys)
sxx = sum(x * x for x in xs)
sxy = sum(x * y for x, y in zip(xs, ys))
period_ns = (n * sxy - sx * sy) / (n * sxx - sx * sx)
A_ref = obs[-1][1] + (sy - period_ns * sx) / n   # est. arrival time of sync closing b_ref
timer_end = round(period_ns * FCLK_HZ / 1e9) - 1
offset_ns = FRAC * period_ns
intended_timer = int(FRAC * (timer_end + 1))
SPIN_NS = int(min(3e6, period_ns / 3))   # pre-deadline spin window
LEAD_NS = int(max(5e6, 2 * period_ns))   # min planning lead (park in recv first)
print("# locked: bucket=%d period=%.4f ms -> TIMER_END~%d, intended offset %.2f ms"
      % (b_ref, period_ns / 1e6, timer_end, offset_ns / 1e6))

# Flywheel model: last_edge advances by exactly one period per tick (bucket
# numbering from the sync stream is exact even when arrival times are not).
# Sync arrival residuals couple in weakly and only when sane; FIRST_ARR errors
# trim phase (corr) and rate (period) with clamped gains and outlier rejection.
KP, K_EDGE = 0.4, 0.05
CORR_STEP_MAX_NS  = 1_000_000     # phase step clamp: 1 ms
PER_STEP_MAX_NS   = 500           # rate step clamp: 500 ns (~5 ppm @ 100 ms)
ERR_SANE_US       = 5_000.0       # ignore larger errors for the servo
RESID_SANE_NS     = 1.5e6
corr_ns = 0.0
cur_bkt = b_ref + 1
last_edge_ns = A_ref              # est. host time of the edge that opened cur_bkt
bad_resid = 0
plan = None
pending = {}
results = []
ident = 2

def clamp(v, lim):
    return -lim if v < -lim else (lim if v > lim else v)

def t_send(k):
    return last_edge_ns + (k - cur_bkt) * period_ns + offset_ns - corr_ns

def spin_until(t_ns):
    while time.monotonic_ns() < t_ns:
        pass

while len(results) < NBKT:
    now = time.monotonic_ns()
    if plan is None:
        k = cur_bkt
        while t_send(k) < now + LEAD_NS:   # need lead time to park in recv first
            k += 1
        plan = (k, int(t_send(k)))
    if now >= plan[1] - SPIN_NS:
        target, deadline = plan
        plan = None
        if target not in pending:
            spin_until(deadline)
            tx.send(rsp_probe(target, ident))
            pending[target] = intended_timer
            ident += 2
        continue
    rx.settimeout(max(0.0005, (plan[1] - SPIN_NS - now) / 1e9))
    try:
        fr = rx.recv(2048)
    except socket.timeout:
        continue
    t = time.monotonic_ns()
    s = parse_sync(fr)
    if not s:
        continue
    closed, first_arr = s
    nt = closed + 1 - cur_bkt
    if nt <= 0:
        continue
    last_edge_ns += nt * period_ns          # flywheel
    cur_bkt = closed + 1
    plan = None                             # replan off the fresh edge
    resid = t - last_edge_ns
    if abs(resid) < RESID_SANE_NS:
        last_edge_ns += K_EDGE * resid      # weak arrival coupling
        bad_resid = 0
    else:
        bad_resid += 1
        if bad_resid >= 10:                 # model truly lost -> hard re-anchor
            last_edge_ns, bad_resid = t, 0
            print("# hard re-anchor to sync arrivals")
    if closed in pending:
        intended = pending.pop(closed)
        hit = first_arr != NOARR
        err_us = (first_arr - intended) / FCLK_HZ * 1e6 if hit else None
        if hit and abs(err_us) < ERR_SANE_US:
            # landed late -> positive err -> send earlier; persistent bias = rate
            corr_ns += clamp(KP * err_us * 1000.0, CORR_STEP_MAX_NS)
            period_ns -= clamp(0.02 * err_us * 1000.0, PER_STEP_MAX_NS)
        results.append({"bucket": closed, "hit": hit, "err_us": err_us})
    for k in [k for k in pending if k < cur_bkt - 2]:   # lost probe or sync
        pending.pop(k)
        results.append({"bucket": k, "hit": False, "err_us": None})

hits = [r for r in results if r["hit"]]
miss = len(results) - len(hits)
print("# %d probes: %d hit, %d miss (%.2f%% miss)"
      % (len(results), len(hits), miss, 100.0 * miss / max(1, len(results))))
if hits:
    warm = min(20, len(hits) // 4)
    conv = sorted(r["err_us"] for r in hits[warm:])
    n = len(conv)
    mean = sum(conv) / n
    std = (sum((e - mean) ** 2 for e in conv) / n) ** 0.5
    print("# landing error vs intended offset (us), n=%d after warmup:" % n)
    print("#   mean=%+.2f std=%.2f  p50=%+.2f p90=%+.2f p99=%+.2f  min=%+.2f max=%+.2f"
          % (mean, std, conv[n // 2], conv[int(n * .9)], conv[min(n - 1, int(n * .99))],
             conv[0], conv[-1]))
print(json.dumps({"iface": IFACE, "tuned": TUNED, "frac": FRAC,
                  "period_ms": period_ns / 1e6, "timer_end": timer_end,
                  "probes": len(results), "miss": miss,
                  "errs_us": [round(r["err_us"], 3) for r in hits]}))
