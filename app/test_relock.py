"""Drive the relock state machine off a synthetic sync stream -- no hardware.

A fake flywheel thread feeds _on_sync() at ~1 kHz with real monotonic timestamps,
which is the only thing relock() cares about. That lets the drifted-period case be
induced on demand instead of waiting hours for the real one.
"""
import collections, os, sys, threading, time
sys.path.insert(0, __import__("os").path.dirname(os.path.abspath(__file__)))
import canon_tx
from canon_tx import CanonPort, NOARR

PERIOD_NS = 1_000_000.0

def make_port():
    p = object.__new__(CanonPort)                 # no sockets, no NIC
    p.iface, p.verbose, p.req = "fake0", True, False
    p._lock = threading.Lock()
    p.period_ns = PERIOD_NS
    p.cur_bkt = 1000
    p.last_edge_ns = time.monotonic_ns()
    p.resid_sane_ns = min(1.5e6, PERIOD_NS / 3)
    p.spin_ns = 62_500; p.lead_ns = 112_500
    p.intended_ticks = int(0.5 * PERIOD_NS * canon_tx.FCLK_HZ / 1e9)
    p.corr_ns = 0.0
    p._per_ref = (p.cur_bkt, p.last_edge_ns)
    p._relock, p._relocks, p._period_at_lock = None, 0, PERIOD_NS
    p._probe_pending, p._probe_hits, p._confirmed = {}, 0, set()
    p._last_confirmed_ns = p._last_corr_log_ns = 0
    p._sent_unconfirmed = {}
    p.sent_log = collections.OrderedDict()
    p.unconfirmed_log = collections.OrderedDict()
    p.sent_log_max = 4000
    return p

p = make_port()
stop = threading.Event()

def flywheel():
    # Derive the bucket number from elapsed time rather than counting sleeps: a
    # Python sleep(1ms) really takes ~1.14ms, which would make the synthetic stream
    # a 1.14ms one and tell us nothing about a 1ms port.
    t0, bkt0 = time.monotonic_ns(), p.cur_bkt
    last = bkt0
    while not stop.is_set():
        time.sleep(0.0004)
        now = time.monotonic_ns()
        bkt = bkt0 + int((now - t0) / PERIOD_NS)
        if bkt > last:
            last = bkt
            with p._lock:
                p._on_sync(bkt, NOARR, now)

th = threading.Thread(target=flywheel, daemon=True); th.start()
time.sleep(0.2)
fails = []
def check(name, cond, detail=""):
    print("  %-46s %s %s" % (name, "OK " if cond else "FAIL", detail))
    if not cond: fails.append(name)

print("1. healthy relock while the flywheel runs")
p.relock(window_s=0.4)
check("period stays within 2% of truth", abs(p.period_ns - PERIOD_NS) < PERIOD_NS*0.02,
      "%.4f ms" % (p.period_ns/1e6))
check("phase still advancing", p.cur_bkt > 1000)

print("2. drifted period -- the wedge this fixes")
with p._lock:
    p.period_ns = PERIOD_NS * 1.06
before = p.period_ns
p.relock(window_s=0.4)
check("re-measure pulled the period back", abs(p.period_ns - PERIOD_NS) < PERIOD_NS*0.02,
      "%.4f -> %.4f ms" % (before/1e6, p.period_ns/1e6))

print("3. guard rejects a re-measure far from the lock-time anchor")
with p._lock:
    p._period_at_lock = PERIOD_NS * 2.0
try:
    p.relock(window_s=0.4)
    check("guard fires", False, "no exception raised")
except RuntimeError as e:
    check("guard fires", "disagrees" in str(e), str(e)[:60] + "...")
with p._lock:
    p._period_at_lock = PERIOD_NS

print("4. a drifted period cannot veto its own correction")
with p._lock:
    p.period_ns = PERIOD_NS * 1.09      # >10% away from the truth it must adopt
p.relock(window_s=0.4)
check("adopted despite the in-use period being far off",
      abs(p.period_ns - PERIOD_NS) < PERIOD_NS*0.02, "%.4f ms" % (p.period_ns/1e6))

print("5. dead flywheel -- relock must report, not hang")
stop.set(); th.join(timeout=2)
t0 = time.monotonic()
try:
    p.relock(window_s=0.3, timeout_s=1.0)
    check("times out cleanly", False, "no exception raised")
except RuntimeError as e:
    check("times out cleanly", "not being fed" in str(e),
          "%.1fs: %s" % (time.monotonic()-t0, str(e)[:52]))

print("6. relock left no armed state behind")
check("_relock cleared", p._relock is None)
print()
print("FAILURES: %s" % (fails or "none"))
sys.exit(1 if fails else 0)
