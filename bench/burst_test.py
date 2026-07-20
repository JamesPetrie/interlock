"""Full-bandwidth bucket burst test against the interlock.

Fills each bucket window with paced max-size RSP-format canonical packets
(declared bucket stamped per frame, ids ascending within the bucket), keeping
the NIC queue shallow so wire time tracks send time. Acceptance is counted
from the far NIC's rx_packets delta (the interlock forwards only accepted
frames), corrected for the sync/cert streams.

Phase tracking: flywheel + FIRST_ARR servo (see calib_probe.py), plus an
integer-bucket BOOTSTRAP: sync frames reach userspace ~2 ms after the actual
tick (measured; r8127 delivery latency), so the arrival-anchored model can lag
by multiple buckets when buckets are small. The bootstrap cycles a declared-
bucket offset delta over successive probe buckets and locks the delta whose
probes get accepted (FIRST_ARR set on the sync closing the declared bucket).
The FIRST_ARR servo then trims the fractional part, with an asymmetric gate:
a dropped first frame only ever shifts the observed error UP (next frame in
the pace grid), so updates more than 300 us above the tracked center are
rejected while the downward path stays open.

usage: burst_test.py <send_iface> <far_iface> <seconds> [util] [--tuned]
"""
import socket, struct, sys, time, os, json, ctypes

class _bpf_ins(ctypes.Structure):
    _fields_ = [("code", ctypes.c_uint16), ("jt", ctypes.c_uint8),
                ("jf", ctypes.c_uint8), ("k", ctypes.c_uint32)]

class _bpf_prog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_uint16), ("filter", ctypes.POINTER(_bpf_ins))]

_bpf_keep = []                     # keep ctypes arrays alive past setsockopt

def attach_filter(sock, prog_ins):
    """SO_ATTACH_FILTER with a tiny cBPF program (list of (code,jt,jf,k))."""
    arr = (_bpf_ins * len(prog_ins))(*prog_ins)
    prog = _bpf_prog(len(prog_ins), arr)
    _bpf_keep.append((arr, prog))
    sock.setsockopt(socket.SOL_SOCKET, 26,
                    ctypes.string_at(ctypes.byref(prog), ctypes.sizeof(prog)))

# accept only 64-byte-DATA 802.3 frames (syncs); the other direction's
# forwarded flood (1514B) must never reach userspace or the drain loop drowns
SYNC_ONLY = [(0x28, 0, 0, 12), (0x15, 0, 1, 64), (0x06, 0, 0, 0x40000), (0x06, 0, 0, 0)]
DROP_ALL  = [(0x06, 0, 0, 0)]

IFACE  = sys.argv[1]
FAR    = sys.argv[2]
SECS   = float(sys.argv[3])
UTIL   = float(sys.argv[4]) if len(sys.argv) > 4 and not sys.argv[4].startswith("--") else 0.8
TUNED  = "--tuned" in sys.argv
REQ    = "--req" in sys.argv    # prod request direction: ids globally monotonic
                                # (REQ prev_id never resets; RSP resets per bucket)
# REQ prev_id never resets in the interlock; start above any previous run's
# high-water mark (us epoch, doubled -> even, monotonic across runs)
next_id = int(time.time() * 1e6) * 2

FCLK_HZ    = 80_000_000
SYNC_ID    = 1
HDR_BYTES  = 64
NOARR      = 0xFFFFFFFF
PLD_LEN    = 1436                     # DATA = 64 hdr + 1436 = 1500 (max canonical)
FRAME_LEN  = 14 + HDR_BYTES + PLD_LEN # 1514 on-wire (sans FCS)
WIRE_NS    = (FRAME_LEN + 24) * 8     # +preamble/FCS/IFG -> ns per frame at 1G
BANK_CAP   = 300_000                  # stay under the 320 KB bucket bank

if TUNED:
    try:
        os.sched_setaffinity(0, {os.cpu_count() - 1})
        os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(50))
    except OSError as e:
        print("# WARNING tuned mode unavailable: %s" % e)

rx = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(0x0003))
rx.bind((IFACE, 0))
rx.setsockopt(263, 1, struct.pack("iHH8s", socket.if_nametoindex(IFACE), 1, 0, b""))
rx.setsockopt(263, 23, 1)          # PACKET_IGNORE_OUTGOING: don't loop back our TX
attach_filter(rx, SYNC_ONLY)
tx = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
tx.bind((IFACE, 0))
# hold the far NIC promiscuous for the whole run so rx_packets counts the
# interlock-station-MAC frames (this socket is never read)
far_promisc = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(0x0003))
far_promisc.bind((FAR, 0))
far_promisc.setsockopt(263, 1, struct.pack("iHH8s", socket.if_nametoindex(FAR), 1, 0, b""))
far_promisc.setsockopt(263, 23, 1)
attach_filter(far_promisc, DROP_ALL)   # promisc membership only; deliver nothing

frame = bytearray(b"\xde\xad\xbe\xef\x00\x02" + b"\xde\xad\xbe\xef\x00\x01"
                  + struct.pack("!H", HDR_BYTES + PLD_LEN)
                  + struct.pack("!IIQ", PLD_LEN, 0, 2) + b"\x00" * 48
                  + bytes(range(256)) * 6)[:FRAME_LEN]
mv = memoryview(frame)

def stamp(bucket, ident):
    mv[18:22] = (bucket & 0xFFFFFFFF).to_bytes(4, "big")
    mv[22:30] = ident.to_bytes(8, "big")

def parse_sync(fr):
    if len(fr) < 14 + HDR_BYTES or fr[12:14] != struct.pack("!H", HDR_BYTES):
        return None
    p = fr[14:14 + HDR_BYTES]
    first_arr, bucket = struct.unpack("!II", p[0:8])
    if int.from_bytes(p[8:16], "big") != SYNC_ID:
        return None
    return bucket, first_arr

def rd_stat(iface, name):
    with open("/sys/class/net/%s/statistics/%s" % (iface, name)) as f:
        return int(f.read())

# --- lock (least-squares over 30 syncs) ------------------------------------
rx.settimeout(1.0)
obs = []
t0 = time.monotonic_ns()
# span-ratio lock: arrivals are batched (delivery jitter up to ~ms), so the
# period comes from total-span / total-buckets over a >=2 s window, where the
# endpoint jitter amortizes to ~us/bucket -- inside the rate servo's range.
while len(obs) < 50 or (obs[-1][1] - obs[0][1]) < 2e9:
    if time.monotonic_ns() - t0 > 60e9:
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
period_ns = (obs[-1][1] - obs[0][1]) / (obs[-1][0] - obs[0][0])
A_ref = obs[-1][1]
timer_end = round(period_ns * FCLK_HZ / 1e9) - 1

SPIN_NS = int(min(1_500_000, max(50_000, period_ns / 16)))
LEAD_NS = SPIN_NS + 50_000
GUARD_LO_NS = max(60_000, period_ns * 0.06)
GUARD_HI_NS = max(250_000, period_ns * 0.08)   # slack for send-start scatter
window_ns   = period_ns - GUARD_LO_NS - GUARD_HI_NS
budget      = min(int(window_ns * UTIL / WIRE_NS), BANK_CAP // FRAME_LEN)
pace_ns     = WIRE_NS / UTIL if budget * FRAME_LEN < BANK_CAP else window_ns / budget
intended_timer = int(GUARD_LO_NS * FCLK_HZ / 1e9)
print("# locked: bucket=%d period=%.4f ms TIMER_END~%d | %d frames/bucket, "
      "guard %.0f/%.0f us, offered ~%.0f Mb/s"
      % (b_ref, period_ns / 1e6, timer_end, budget, GUARD_LO_NS / 1e3,
         GUARD_HI_NS / 1e3, budget * FRAME_LEN * 8 / (period_ns / 1e9) / 1e6))

K_EDGE = 0.05
RESID_SANE_NS = min(1.5e6, period_ns / 3)
cur_bkt, last_edge_ns = b_ref + 1, A_ref
DECL_SHIFT = None                 # integer-bucket offset, from bootstrap
servo_on = False
sent = buckets_filled = servo_hits = servo_rej = 0
errs = []
# Edge-fit estimator: each accepted FIRST_ARR yields one exact observation of
# a true bucket edge in host time (send_t0 - first_arr*12.5ns). A rolling
# least-squares line over these gives phase+rate directly (~10 us noise) and,
# unlike incremental feedback, is immune to the ~2-bucket feedback delay.
sent_log = {}                     # declared bucket -> host t of its first frame
edge_obs = []                     # (bucket, edge_host_ns), rolling window
fit = None                        # (k0, e0, per) -> edge(k) = e0 + (k-k0)*per

def refit():
    global fit
    if len(edge_obs) < 4:
        return
    k0 = edge_obs[-1][0]
    xs = [k - k0 for k, _ in edge_obs]; ys = [e - edge_obs[-1][1] for _, e in edge_obs]
    n = len(xs); sx, sy = sum(xs), sum(ys)
    sxx = sum(x * x for x in xs); sxy = sum(x * y for x, y in zip(xs, ys))
    d = n * sxx - sx * sx
    if d == 0:
        return
    per = (n * sxy - sx * sy) / d
    e0 = edge_obs[-1][1] + (sy - per * sx) / n
    if abs(per - period_ns) < period_ns * 0.01:
        fit = (k0, e0, per)

def edge_of(k):
    if fit:
        k0, e0, per = fit
        return e0 + (k - k0) * per
    return last_edge_ns + (k - cur_bkt) * period_ns

def next_send(offset_ns):
    """Pick the next declared bucket D and its send time. In fit mode the fit's
    numbering IS the declared numbering (edge_obs are keyed by declared bucket);
    DECL_SHIFT applies only in the pre-fit flywheel frame."""
    now = time.monotonic_ns()
    if fit:
        D = cur_bkt + DECL_SHIFT
        while edge_of(D) + offset_ns < now + LEAD_NS:
            D += 1
        return D, edge_of(D) + offset_ns
    k = cur_bkt
    while last_edge_ns + (k - cur_bkt) * period_ns + offset_ns < now + LEAD_NS:
        k += 1
    return k + DECL_SHIFT, last_edge_ns + (k - cur_bkt) * period_ns + offset_ns

def clamp(v, lim):
    return -lim if v < -lim else (lim if v > lim else v)

boot_pending = {}                 # declared_bucket -> delta
boot_hits = {}

def on_sync(closed, first_arr, t):
    """Shared flywheel bookkeeping + FIRST_ARR servo. Returns True on new tick."""
    global cur_bkt, last_edge_ns, period_ns
    global servo_hits, servo_rej, DECL_SHIFT, servo_on
    nt = closed + 1 - cur_bkt
    if nt <= 0:
        return False
    last_edge_ns += nt * period_ns
    cur_bkt = closed + 1
    resid = t - last_edge_ns
    if abs(resid) < RESID_SANE_NS:
        last_edge_ns += K_EDGE * resid
    if DECL_SHIFT is None:
        if closed in boot_pending and first_arr != NOARR:
            d = boot_pending.pop(closed)
            boot_hits[d] = boot_hits.get(d, 0) + 1
            if boot_hits[d] >= 4:
                DECL_SHIFT = d
                print("# bootstrap: declared-bucket shift = +%d (hits %s)" % (d, boot_hits))
        for k in [k for k in boot_pending if k < cur_bkt - 1]:
            boot_pending.pop(k)
        return True
    if servo_on and first_arr != NOARR and closed in sent_log:
        t0 = sent_log.pop(closed)
        eo = t0 - first_arr * 1e9 / FCLK_HZ
        if fit is None or abs(eo - edge_of(closed)) < 300_000:
            edge_obs.append((closed, eo))
            if len(edge_obs) > 32:
                edge_obs.pop(0)
            if fit is None or servo_hits % 4 == 0:
                refit()
            errs.append((first_arr - intended_timer) / FCLK_HZ * 1e6)
            servo_hits += 1
        else:
            servo_rej += 1
    for kk in [kk for kk in sent_log if kk < cur_bkt - 4]:
        sent_log.pop(kk)
    return True

def park_until(t_target):
    """Process syncs until t_target, never overshooting it by more than ~50us."""
    while True:
        now = time.monotonic_ns()
        rem = t_target - now
        if rem <= SPIN_NS:
            rx.setblocking(False)
            try:
                while True:
                    fr = rx.recv(2048)
                    s = parse_sync(fr)
                    if s:
                        on_sync(s[0], s[1], time.monotonic_ns())
            except BlockingIOError:
                pass
            while time.monotonic_ns() < t_target:
                pass
            return
        rx.settimeout((rem - SPIN_NS) / 1e9)
        try:
            fr = rx.recv(2048)
        except socket.timeout:
            continue
        t = time.monotonic_ns()
        s = parse_sync(fr)
        if s:
            on_sync(s[0], s[1], t)

# --- bootstrap: find the integer-bucket declared shift ----------------------
boot_i = 0
t_boot0 = time.monotonic_ns()
while DECL_SHIFT is None:
    if time.monotonic_ns() - t_boot0 > 15e9:
        print("FAIL: bootstrap found no accepted delta (hits %s)" % boot_hits); sys.exit(1)
    base = cur_bkt
    park_until(edge_of(cur_bkt) + 0.5 * period_ns)  # mid-bucket of current model bucket
    if cur_bkt != base:                            # a tick landed meanwhile; replan
        continue
    d = boot_i % 8
    stamp(cur_bkt + d, next_id if REQ else 2)
    next_id += 2
    tx.send(mv)
    boot_pending[cur_bkt + d] = d
    boot_i += 1
    park_until(edge_of(cur_bkt) + 1.02 * period_ns) # into next bucket, then loop

# --- settle: single frames at mid-bucket until the FIRST_ARR servo has
# pulled the model phase to us-scale; only then is a guard_lo target safe ----
servo_on = True

def settle(n_obs):
    # single mid-bucket probes; declared bucket dithered +/-1 around the model's
    # best guess -- the fit is keyed by DECLARED number, so any accepted probe
    # feeds it correctly no matter which offset won.
    intended = int(0.5 * (timer_end + 1))
    globals()["intended_timer"] = intended
    end = cur_bkt + 600
    i = 0
    last_dbg = time.monotonic_ns()
    while cur_bkt < end and not (fit and len(edge_obs) >= n_obs):
        if time.monotonic_ns() - last_dbg > 2e9:
            print("# settle-dbg: i=%d cur_bkt=%d end=%d obs=%d fit=%s pend=%d"
                  % (i, cur_bkt, end, len(edge_obs),
                     "None" if not fit else "%.6fms" % (fit[2] / 1e6), len(sent_log)))
            last_dbg = time.monotonic_ns()
        global next_id
        D, w0 = next_send(0.5 * period_ns)
        D += (i % 6) - 1   # wide dither: USB TX latency shifts whole buckets under rx load
        park_until(w0)
        t0 = time.monotonic_ns()
        stamp(D, next_id if REQ else 2)
        next_id += 2
        tx.send(mv)
        sent_log[D] = t0
        i += 1
    return fit is not None and len(edge_obs) >= n_obs

if not settle(32):
    print("FAIL: settle never converged (obs=%d)" % len(edge_obs)); sys.exit(1)
print("# settle: %d edge obs, fitted period %.6f ms" % (len(edge_obs), fit[2] / 1e6))
errs.clear()
intended_timer = int(GUARD_LO_NS * FCLK_HZ / 1e9)

# --- main fill loop ---------------------------------------------------------
base0 = rd_stat(FAR, "rx_packets"); time.sleep(2.0)
base_rate = (rd_stat(FAR, "rx_packets") - base0) / 2.0   # far-NIC background pps
far_rx0 = rd_stat(FAR, "rx_packets")
t_start = time.monotonic_ns()
deadline_ns = t_start + SECS * 1e9

relock = 0
last_hits = 0
silent = 0
while time.monotonic_ns() < deadline_ns:
    if servo_hits == last_hits:
        silent += 1
        if silent > 300:               # feedback lost -> re-lock rather than blast blind
            relock += 1
            edge_obs.clear()
            settle(16)
            errs.clear()
            intended_timer = int(GUARD_LO_NS * FCLK_HZ / 1e9)
            silent = 0
    else:
        silent = 0
    last_hits = servo_hits
    D, w0 = next_send(GUARD_LO_NS)
    park_until(w0)
    t_next = w0
    t0 = time.monotonic_ns()
    for i in range(budget):
        if REQ:
            stamp(D, next_id)
            next_id += 2
        else:
            stamp(D, 2 * (i + 1))
        tx.send(mv)
        sent += 1
        t_next += pace_ns
        while time.monotonic_ns() < t_next:
            pass
    sent_log[D] = t0
    buckets_filled += 1

elapsed_s = (time.monotonic_ns() - t_start) / 1e9
time.sleep(1.5)
far_rx = rd_stat(FAR, "rx_packets") - far_rx0
overhead = int(base_rate * (elapsed_s + 1.5)) + 2
accepted = far_rx - overhead
dropped = sent - accepted
print("# %.1f s, %d buckets filled, %d frames sent (offered %.0f Mb/s of data)"
      % (elapsed_s, buckets_filled, sent, sent * FRAME_LEN * 8 / elapsed_s / 1e6))
print("# far NIC rx=%d (sync/cert overhead est %d) -> accepted=%d, dropped=%d (%.4f%%)"
      % (far_rx, overhead, accepted, max(0, dropped),
         100.0 * max(0, dropped) / max(1, sent)))
print("# servo: %d updates, %d rejected by gate, %d relocks" % (servo_hits, servo_rej, relock))
if errs:
    e = sorted(errs[min(20, len(errs) // 4):])
    m = len(e)
    print("# first-frame landing err vs guard_lo (us): p50=%+.1f p99=%+.1f min=%+.1f max=%+.1f (n=%d)"
          % (e[m // 2], e[min(m - 1, int(m * .99))], e[0], e[-1], m))
print(json.dumps({"sent": sent, "accepted": accepted, "buckets": buckets_filled,
                  "elapsed_s": round(elapsed_s, 2), "util": UTIL,
                  "period_ms": period_ns / 1e6, "decl_shift": DECL_SHIFT,
                  "offered_mbps": round(sent * FRAME_LEN * 8 / elapsed_s / 1e6, 1),
                  "servo_hits": servo_hits, "servo_rej": servo_rej}))
