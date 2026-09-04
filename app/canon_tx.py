"""Bucket-timed canonical port for the interlock app (the §0 "send-path port").

The prod-1ms gateware accepts a frame only if it *physically arrives during the
bucket it declares*, so the app can no longer just `send()` a frame the moment it
has one -- the pre-bucket wire format `infcli.py`/`model_server.py` were written
against is dropped outright. This module wraps a NIC as a canonical port that
locks onto the device's 1 kHz sync stream and places each frame inside a declared
bucket.

The timing machinery (flywheel, declared-bucket bootstrap, edge servo) is the one
proven in `bench/burst_test.py`, reduced to what the app actually needs: the app
sends one small packet at a time with big gaps between them, never a full-rate
burst, so there is no pacing or bank-capacity problem to solve -- only placement.

Canonical wire format:

    dst(6) || src(6) || len(2) || hdr(64) || payload
    hdr = !IIQ(len(payload), declared_bucket, ident) || 48 zero bytes

Direction matters for `ident`: the interlock's REQ-side `prev_id` never resets, so
a client that restarts and reuses low ids is silently rejected 100% of the time.
Request senders therefore seed `ident` from the microsecond epoch. Response ids
reset per bucket, so the server can use a fixed low id.

Typical use:

    port = CanonPort("eth0", req=True)      # client side (port 1 / J30)
    port.lock(); port.bootstrap()
    port.send(payload)
    data = port.recv(timeout=5.0)           # payload of the next non-sync frame
"""
import collections
import os
import socket
import struct
import sys
import threading
import time

FCLK_HZ = 80_000_000          # fabric clock: FIRST_ARR / timer units
SYNC_ID = 1                   # canonical id reserved for sync frames
HDR_BYTES = 64                # canonical header
NOARR = 0xFFFFFFFF            # first_arr when a bucket accepted nothing
PLD_MAX = 1436                # 64 hdr + 1436 = 1500 max canonical DATA

K_EDGE = 0.05                 # weak arrival coupling into the flywheel phase
# Where in the bucket to place a frame. bench/README.md says send as EARLY as possible,
# but that advice is for full-rate bursts, where margin is period − offset − burst_length.
# The app sends one small frame at a time, so the binding constraint is the flywheel's
# uncorrected phase error (~100 us, which calib_probe.py only servos away over many
# probes). Mid-bucket trades away throughput headroom we do not need for ±half a period
# of timing tolerance, which is what makes single sends land reliably without a servo.
SEND_FRAC = 0.5
KP_SERVO = 0.25              # FIRST_ARR landing-error feedback gain
CORR_STEP_MAX_NS = 50_000    # clamp per-observation correction
# Keeping the port warm, WITHOUT bursting. Sends that follow one another closely land
# reliably; a send after seconds of silence does not, and the servo cannot recover on its
# own because it only learns from frames the device accepted. The obvious fix -- a burst
# of probes just before a real send -- violates the interlock's one-packet-in-flight,
# spaced discipline (bench README: "a flood wedges the interlock") and did wedge it. So
# instead trickle one header-only probe at a low, fixed rate: the servo always has fresh
# feedback, placement is never cold, and the wire never sees a burst.
KEEPALIVE_S = 0.25           # seconds between idle keep-alive probes (4/s)
BOOTSTRAP_GAP_S = 0.02       # spacing between bootstrap probes (50/s)
# Servo anti-windup. The bound must sit OUTSIDE the loop's normal operating range or it
# becomes the fault it was meant to prevent: measured equilibrium on this host is ~250 us
# of real transmit latency (schedule -> wire) with ~40 us of hunting around it, so a flat
# 0.30-of-a-period bound (300 us) clipped on ordinary excursions -- and a clip walks the
# frame out of its declared bucket, which stops the acceptances the servo needs to unwind,
# latching the port. What actually matters is that the correction never pulls the send
# past the START of its bucket, so bound it by the room SEND_FRAC leaves, keeping
# CORR_HEADROOM_FRAC of a period in hand.
CORR_HEADROOM_FRAC = 0.10    # never place earlier than this far into the bucket
# The bucket period is measured once at lock() against THIS host's monotonic clock, which
# is not the fabric's clock -- the two differ by a crystal tolerance (the Spark locked at
# 0.9997 ms where the Pi read 1.0000 ms on the same stream). A fixed period estimate turns
# that ratio error into a phase ramp: the flywheel's weak arrival coupling absorbs some of
# it, the placement servo integrates the rest, and after minutes of idle `corr_ns` walks
# into its anti-windup bound and latches every send into rejection. So re-fit the period
# continuously over a long baseline, where per-frame arrival jitter amortizes away.
PERIOD_REFIT_BUCKETS = 5000  # ~5 s baseline per re-fit
PERIOD_GAIN = 0.25           # how much of each re-fit to adopt
PERIOD_SANE_FRAC = 0.01      # reject a re-fit that disagrees by more than this
CORR_LOG_S = 30.0            # throttle for the servo-state log line
# Re-lock, the second tier of recover(). Zeroing the servo correction only helps if the
# correction was the fault; if the period estimate itself has drifted, placement stays
# wrong at ANY correction and the re-bootstrap fails too. Re-measuring is then the only
# thing left short of a restart -- which is what an operator ends up doing by hand.
RELOCK_WINDOW_S = 2.5        # baseline for the re-measurement, same as lock()
RELOCK_MIN_OBS = 30          # refuse to re-derive a period from fewer samples
RELOCK_SANE_FRAC = 0.10      # reject a re-measure this far from the period in use


class CanonPort:
    def __init__(self, iface, *, req=False, tuned=True, verbose=True,
                  accept_dst=None, accept_src=None):
        """`accept_dst`/`accept_src` filter received frames by the interlock's
        forced MACs. Without them `recv()` also returns our own transmissions --
        AF_PACKET with ETH_P_ALL sees outgoing frames too, which would loop a
        server straight back into itself."""
        self.iface, self.req, self.verbose = iface, req, verbose
        self.accept_dst, self.accept_src = accept_dst, accept_src
        self.rx = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(0x0003))
        self.rx.bind((iface, 0))
        # Forwarded frames carry the interlock's forced DST, not this NIC's MAC.
        self.rx.setsockopt(263, 1, struct.pack("iHH8s",
                                               socket.if_nametoindex(iface), 1, 0, b""))
        self.tx = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
        self.tx.bind((iface, 0))
        if tuned:
            self._tune()
        # REQ ids must be globally monotonic across runs; RSP ids reset per bucket.
        self.next_id = self._req_id_seed() if req else 2
        self.cur_bkt = self.last_edge_ns = None
        self.period_ns = 1_000_000.0
        self.decl_shift = None
        self.corr_ns = 0.0            # servo correction, from FIRST_ARR feedback
        self._last_confirmed_ns = 0   # when the device last confirmed one of our frames
        self._per_ref = None          # (bucket, t) baseline for the slow period re-fit
        self._last_corr_log_ns = 0
        self._recoveries = 0
        self._relock = None           # armed re-measurement, serviced by the flywheel
        self._period_at_lock = None   # anchor for sanity-checking a re-measure
        self._relocks = 0
        # Everything we put on the wire, indexed by declared bucket. The certificate
        # commits a whole period, so auditing one of our packets means accounting for
        # ALL our traffic in that period -- bootstrap probes included.
        self.sent_log = collections.OrderedDict()
        self.sent_log_max = 4000
        # Frames the device never acknowledged. Usually that means it rejected them
        # (placement slipped), and a rejected frame is not committed -- but a sync
        # LOST on the way in looks identical from here, and in that case the device
        # did commit the frame. Discarding these outright made that case unauditable:
        # the epoch root came out wrong and the only available reading was tampering.
        # Keep them so commit.audit_search can ask the certificate which it was.
        self.unconfirmed_log = collections.OrderedDict()
        self._probe_pending = {}      # declared bucket -> send time, awaiting FIRST_ARR
        self._probe_hits = 0          # declared buckets the device confirmed accepting
        self._confirmed = set()       # declared buckets whose frame the device accepted
        self._sent_unconfirmed = {}   # declared bucket -> DATA awaiting confirmation
        # The flywheel runs in its own thread. An app that sends sporadically (a chat
        # turn, then silence) cannot service the sync stream from its main loop, and a
        # phase reference rebuilt from one freshly-read sync carries that sync's full
        # scheduling jitter into the send. Keeping the wheel turning continuously is
        # what lets a single send land in the bucket it declares.
        self._lock = threading.Lock()
        self._tx_lock = threading.Lock()   # one packet in flight at a time
        self._last_tx_ns = 0
        self._ka_thread = None
        self._rxq = collections.deque(maxlen=512)     # non-sync frames, for recv()
        self._stop = threading.Event()
        self._thread = None

    # ---------------------------------------------------------------- setup

    # Where the REQ-id floor persists, and how many ids a run may claim above it.
    # 2^21 ids is ~1M sends -- and only ~1 s of microsecond-clock headroom, so a
    # healthy clock overtakes the written floor almost immediately and the file
    # changes nothing. It exists for the unhealthy clock.
    ID_FLOOR_FILE = os.environ.get(
        "ILK_IDFLOOR_FILE", os.path.expanduser("~/.interlock/req_id_floor"))
    ID_FLOOR_RESERVE = 1 << 21

    def _req_id_seed(self):
        """First REQ ident for this process: the clock, floored by the last run.

        The device's REQ-side prev_id high-water mark never resets, so idents
        must be monotonic ACROSS runs -- and the microsecond clock alone cannot
        promise that on a Pi: no RTC, so a boot that outruns NTP starts with a
        stale clock, seeds ids below the high-water mark, and every frame is
        rejected in silence (bootstrap fails with only the prev_id hint to go
        on). So each start also reads the floor the previous run wrote, takes
        the max, and writes a new floor above what it might use. One file read
        and one atomic write, at open only -- nothing on the send path."""
        clock = int(time.time() * 1e6) * 2
        floor = 0
        try:
            with open(self.ID_FLOOR_FILE) as fh:
                floor = int(fh.read().strip() or 0)
        except (OSError, ValueError):
            pass
        nid = max(clock, floor)
        try:
            os.makedirs(os.path.dirname(self.ID_FLOOR_FILE), exist_ok=True)
            tmp = self.ID_FLOOR_FILE + ".tmp"
            with open(tmp, "w") as fh:
                fh.write("%d\n" % (nid + self.ID_FLOOR_RESERVE))
            os.replace(tmp, self.ID_FLOOR_FILE)
        except OSError as e:
            self._log("WARNING: cannot persist the REQ id floor (%s) -- a clock "
                      "regression across restarts will strand the ids below the "
                      "device's high-water mark" % e)
        if nid > clock:
            self._log("REQ ids seeded from the floor file, %+d over the clock -- "
                      "the clock is behind (NTP not synced yet?)" % (nid - clock))
        return nid

    def _tune(self):
        """RT priority + core pinning. Failure is fatal for timing accuracy, so
        say so loudly rather than silently producing untuned placement."""
        try:
            os.sched_setaffinity(0, {os.cpu_count() - 1})
            os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(50))
            self._log("tuned: SCHED_FIFO 50, pinned to cpu%d" % (os.cpu_count() - 1))
        except OSError as e:
            self._log("WARNING: RT scheduling unavailable (%s) -- bucket placement "
                      "will be jittery; run as root" % e)

    def _log(self, msg):
        if self.verbose:
            print("# [canon_tx] %s" % msg, flush=True)

    def parse_sync(self, fr):
        """(closed_bucket, first_arr) for a sync frame, else None."""
        if len(fr) < 14 + HDR_BYTES or fr[12:14] != struct.pack("!H", HDR_BYTES):
            return None
        p = fr[14:14 + HDR_BYTES]
        first_arr, bucket = struct.unpack("!II", p[0:8])
        if struct.unpack("!Q", p[8:16])[0] != SYNC_ID:
            return None
        return bucket, first_arr

    def _apply_lock(self, obs):
        """Adopt period, phase and everything derived from (bucket, arrival_ns) samples.

        Shared by lock() and relock() so the two can never derive the flywheel's
        constants differently -- a re-lock that disagreed with the original in some
        small way would be its own bug, and an invisible one."""
        span_bkt = obs[-1][0] - obs[0][0]
        if span_bkt <= 0:
            raise RuntimeError("sync bucket counter did not advance on %s" % self.iface)
        self.period_ns = (obs[-1][1] - obs[0][1]) / span_bkt
        self.cur_bkt = obs[-1][0] + 1
        self.last_edge_ns = obs[-1][1]
        self.spin_ns = int(min(1_500_000, max(50_000, self.period_ns / 16)))
        self.lead_ns = self.spin_ns + 50_000
        self.resid_sane_ns = min(1.5e6, self.period_ns / 3)
        # Intended in-bucket landing point, in FIRST_ARR's fabric-clock ticks.
        self.intended_ticks = int(SEND_FRAC * self.period_ns * FCLK_HZ / 1e9)
        self._per_ref = (obs[-1][0], obs[-1][1])

    def lock(self, window_s=2.5):
        """Measure the bucket period over a window of sync frames. Period comes
        from total-span / total-buckets, where endpoint jitter amortizes away."""
        obs = []
        t0 = time.monotonic_ns()
        while len(obs) < 30 or (time.monotonic_ns() - t0) < window_s * 1e9:
            fr = self.rx.recv(2048)
            t = time.monotonic_ns()
            s = self.parse_sync(fr)
            if s:
                obs.append((s[0], t))
            if (time.monotonic_ns() - t0) > (window_s + 8) * 1e9:
                raise RuntimeError("no sync stream on %s -- wrong port, dead link, "
                                   "or the board is not running prod-1ms" % self.iface)
        self._apply_lock(obs)
        # Deliberately NOT refreshed by relock(): this is the period measured when the
        # port was known good -- bootstrap() succeeds against it moments later -- so it
        # stays the fixed anchor a later re-measure is judged against.
        self._period_at_lock = self.period_ns
        self._log("locked: bucket=%d period=%.4f ms" % (self.cur_bkt, self.period_ns / 1e6))

    # ------------------------------------------------------------ flywheel

    def _on_sync(self, closed, first_arr, t):
        nt = closed + 1 - self.cur_bkt
        if nt <= 0:
            return False
        self.last_edge_ns += nt * self.period_ns
        self.cur_bkt = closed + 1
        resid = t - self.last_edge_ns
        if abs(resid) < self.resid_sane_ns:
            self.last_edge_ns += K_EDGE * resid          # weak arrival coupling
        self._refit_period(closed, t)
        self._service_relock(closed, t)
        if first_arr != NOARR and closed in self._probe_pending:
            self._probe_pending.pop(closed)
            self._probe_hits += 1
            self._confirmed.add(closed)         # the device accepted that bucket's frame
            self._last_confirmed_ns = t
            # FIRST_ARR is the device's own measurement of where in the bucket our frame
            # landed, so it closes the loop on placement. Without it the flywheel sits
            # biased late -- the weak arrival coupling pulls `last_edge_ns` toward sync
            # ARRIVAL time, which trails the true edge by the sync's delivery latency --
            # and sporadic sends drift out of their declared bucket even though rapid
            # ones look fine. The correction persists across idle, which is the point.
            err_ns = (first_arr - self.intended_ticks) * 1e9 / FCLK_HZ
            if abs(err_ns) < self.period_ns:
                step = KP_SERVO * err_ns
                lim = CORR_STEP_MAX_NS
                self.corr_ns += -lim if step < -lim else (lim if step > lim else step)
                # Anti-windup, and it is load-bearing: the correction shifts the send
                # within its bucket, so letting it grow past ~half a period walks the
                # frame into the NEXT bucket while it still declares this one. Every
                # frame is then rejected, which starves the servo of the acceptances it
                # needs to correct itself -- a latch that only a restart clears.
                wmax = self.period_ns * (SEND_FRAC - CORR_HEADROOM_FRAC)
                self.corr_ns = -wmax if self.corr_ns < -wmax else (
                    wmax if self.corr_ns > wmax else self.corr_ns)
            self._log_servo(t)
            if len(self._confirmed) > 4096:
                self._confirmed = set(sorted(self._confirmed)[-2048:])
            # Only accepted frames are committed by the certificate, so the audit log
            # takes a packet solely once the device confirms it. Rejected attempts
            # (a retry's earlier tries) must never enter it.
            d = self._sent_unconfirmed.pop(closed, None)
            if d is not None:
                self.sent_log.setdefault(closed, []).append(d)
                while len(self.sent_log) > self.sent_log_max:
                    self.sent_log.popitem(last=False)
        for k in [k for k in self._sent_unconfirmed if k < self.cur_bkt - 4]:
            # Presumed rejected, hence not committed -- but only presumed, so park it
            # in unconfirmed_log rather than dropping the bytes an audit may need.
            self.unconfirmed_log[k] = [self._sent_unconfirmed.pop(k)]
            while len(self.unconfirmed_log) > self.sent_log_max:
                self.unconfirmed_log.popitem(last=False)
        for k in [k for k in self._probe_pending if k < self.cur_bkt - 2]:
            self._probe_pending.pop(k)          # probe lost or bucket rejected it
        return True

    def _refit_period(self, closed, t):
        """Track the host-clock length of a bucket over a long baseline.

        Caller holds self._lock. One sync arrival is far too jittery to time a 1 ms
        bucket, but that jitter is bounded while the baseline is not, so over
        PERIOD_REFIT_BUCKETS the ratio between this host's clock and the fabric's
        emerges cleanly. Adopting it slowly keeps the flywheel from chasing noise."""
        if self._per_ref is None:
            self._per_ref = (closed, t)
            return
        span = closed - self._per_ref[0]
        if span < PERIOD_REFIT_BUCKETS:
            return
        meas = (t - self._per_ref[1]) / span
        self._per_ref = (closed, t)
        if abs(meas - self.period_ns) > self.period_ns * PERIOD_SANE_FRAC:
            return                                       # scheduling hiccup, not drift
        self.period_ns += PERIOD_GAIN * (meas - self.period_ns)
        # intended_ticks is the landing point in FABRIC ticks, so it follows the period.
        self.intended_ticks = int(SEND_FRAC * self.period_ns * FCLK_HZ / 1e9)

    def _service_relock(self, closed, t):
        """Collect for an armed relock() and, once the window is full, adopt it.

        Caller holds self._lock and has just applied this sync to the flywheel, so
        obs[-1] IS the sample the flywheel is currently sitting on -- which is why
        _apply_lock's phase (cur_bkt, last_edge_ns) stays consistent when it lands
        here rather than being spliced in from another thread."""
        rl = self._relock
        if rl is None or rl["done"] is not None:
            return
        rl["obs"].append((closed, t))
        if t < rl["until"] or len(rl["obs"]) < RELOCK_MIN_OBS:
            return
        old = self.period_ns
        span = rl["obs"][-1][0] - rl["obs"][0][0]
        cand = (rl["obs"][-1][1] - rl["obs"][0][1]) / span if span > 0 else None
        # A re-measure is only worth adopting if it is a correction and not a fresh
        # fault -- garbage here would replace a drifting period with a wrong one, and
        # there is no third tier to catch that. Judge it against the period measured at
        # lock(), NOT the one in use: the one in use is the value under suspicion, and
        # letting it set the bounds would let a badly drifted period veto its own fix.
        ref = self._period_at_lock or old
        if cand is None or abs(cand - ref) > ref * RELOCK_SANE_FRAC:
            rl["reject"] = cand
            rl["done"] = False
            return
        try:
            self._apply_lock(rl["obs"])
        except RuntimeError:
            rl["done"] = False
            return
        rl["old"] = old
        rl["done"] = True

    def relock(self, window_s=RELOCK_WINDOW_S, timeout_s=None):
        """Re-measure period and phase without touching the rx socket.

        lock() reads self.rx directly, which is fine before start() and wrong after:
        from then on the flywheel thread owns that socket, and two consumers on one
        socket steal frames from each other -- the flywheel would miss the syncs it
        needs to hold phase while lock() built its window from whatever was left. So
        this arms the flywheel to collect its own arrivals and re-derive the constants
        in place, under the lock it already updates them with.

        Sending stays paused throughout: the only caller is recover(), which gets here
        after a failed bootstrap() has already set decl_shift = None, which is what the
        keep-alive checks before trickling a probe."""
        if timeout_s is None:
            timeout_s = window_s + 6.0
        box = {"obs": [], "until": time.monotonic_ns() + int(window_s * 1e9),
               "done": None}
        with self._lock:
            self._relock = box
        deadline = time.monotonic_ns() + int(timeout_s * 1e9)
        while time.monotonic_ns() < deadline:
            with self._lock:
                if box["done"] is not None:
                    break
            time.sleep(0.02)
        with self._lock:
            self._relock = None
            done, obs_n = box["done"], len(box["obs"])
        if done is None:
            raise RuntimeError("relock: only %d syncs in %.1fs on %s -- the flywheel is "
                               "not being fed" % (obs_n, timeout_s, self.iface))
        if done is False:
            ref = self._period_at_lock or self.period_ns
            raise RuntimeError("relock: re-measured period %s disagrees with the %.4f ms "
                               "measured at lock by more than %.0f%% -- kept the old one"
                               % ("%.4f ms" % (box["reject"] / 1e6) if box.get("reject")
                                  else "(unusable)", ref / 1e6, 100 * RELOCK_SANE_FRAC))
        self._relocks += 1
        self._log("relock #%d: period %.4f -> %.4f ms, phase re-acquired from %d syncs"
                  % (self._relocks, box["old"] / 1e6, self.period_ns / 1e6, obs_n))

    def _log_servo(self, t):
        """Caller holds self._lock. Periodic servo state -- this is the signal that
        distinguishes a converged loop from one ramping toward its bound."""
        if t - self._last_corr_log_ns < CORR_LOG_S * 1e9:
            return
        self._last_corr_log_ns = t
        sat = abs(self.corr_ns) >= self.period_ns * (SEND_FRAC - CORR_HEADROOM_FRAC) - 1.0
        self._log("servo: corr=%+.1fus period=%.4fms hits=%d%s"
                  % (self.corr_ns / 1e3, self.period_ns / 1e6, self._probe_hits,
                     "  *** SATURATED -- placement will start failing ***" if sat else ""))

    def _edge_of(self, k):
        return self.last_edge_ns + (k - self.cur_bkt) * self.period_ns

    def _park_until(self, t_target, sink=None):
        """Service the rx queue until t_target, overshooting by at most ~spin_ns.
        Non-sync frames go to `sink` (a list) rather than being dropped."""
        while True:
            now = time.monotonic_ns()
            rem = t_target - now
            if rem <= self.spin_ns:
                self.rx.setblocking(False)
                try:
                    while True:
                        fr = self.rx.recv(2048)
                        s = self.parse_sync(fr)
                        if s:
                            self._on_sync(s[0], s[1], time.monotonic_ns())
                        elif sink is not None:
                            sink.append(fr)
                except BlockingIOError:
                    pass
                self.rx.setblocking(True)
                while time.monotonic_ns() < t_target:
                    pass
                return
            self.rx.settimeout((rem - self.spin_ns) / 1e9)
            try:
                fr = self.rx.recv(2048)
            except socket.timeout:
                continue
            t = time.monotonic_ns()
            s = self.parse_sync(fr)
            if s:
                self._on_sync(s[0], s[1], t)
            elif sink is not None:
                sink.append(fr)

    # ------------------------------------------------------------- framing

    def _frame(self, payload, bucket, ident):
        # MAC order as in bench/calib_probe.py + burst_test.py, the senders the
        # device is known to accept; the interlock overwrites both on forward.
        dst = b"\xde\xad\xbe\xef\x00\x02"
        src = b"\xde\xad\xbe\xef\x00\x01"
        hdr = struct.pack("!IIQ", len(payload), bucket & 0xFFFFFFFF, ident) + b"\x00" * 48
        return (dst + src + struct.pack("!H", HDR_BYTES + len(payload)) + hdr + payload)

    def bootstrap(self, need=4, per_shift=24):
        """Confirm frames land in the bucket they declare, and find the offset if not.

        `send()` plans a target bucket ~lead_ns ahead and parks on its edge -- the
        same discipline as bench/calib_probe.py, which lands 100% of its probes with
        no declared-bucket offset at all. So shift 0 is tried first; the 1..7 scan is
        only a fallback for a device that disagrees. Probes are header-only, so an
        accepted one costs a bucket and nothing else."""
        if self.cur_bkt is None:
            raise RuntimeError("bootstrap() before lock()")
        tried = {}
        for cand in range(8):
            self.decl_shift = cand
            self._probe_hits = 0
            self._probe_pending = {}
            for _ in range(per_shift):
                self.send(b"")
                if self._probe_hits >= need:
                    break
                # Space the probes. Back-to-back they run at ~1/ms, which is a flood by
                # the interlock's standards and is what wedges it; the bootstrap only
                # needs a handful of acceptances, not speed.
                time.sleep(BOOTSTRAP_GAP_S)
            time.sleep(5 * self.period_ns / 1e9)   # let the last probes' syncs land
            tried[cand] = self._probe_hits
            if self._probe_hits >= need:
                self._log("bootstrap: declared-bucket shift = +%d (%d/%d probes accepted)"
                          % (cand, self._probe_hits, per_shift))
                return cand
        self.decl_shift = None
        raise RuntimeError("bootstrap: no declared-bucket offset accepted (hits by shift: "
                           "%s) -- wrong port direction, or the device is rejecting "
                           "everything (REQ prev_id high-water mark?)" % tried)

    def start(self):
        """Begin servicing the sync stream in the background."""
        if self._thread is None:
            self._thread = threading.Thread(target=self._rx_loop, daemon=True)
            self._thread.start()
        if self._ka_thread is None:
            self._ka_thread = threading.Thread(target=self._keepalive_loop, daemon=True)
            self._ka_thread.start()

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _keepalive_loop(self):
        """Trickle a header-only probe whenever the port has been idle, so the
        placement servo never goes cold. Skipped while a real send holds the tx
        lock -- the wire still carries at most one of our frames at a time."""
        while not self._stop.is_set():
            time.sleep(KEEPALIVE_S / 2)
            if self.decl_shift is None:
                continue
            if time.monotonic_ns() - self._last_tx_ns < KEEPALIVE_S * 1e9:
                continue
            if not self._tx_lock.acquire(blocking=False):
                continue
            try:
                self._send_locked(b"")
            except Exception:
                pass
            finally:
                self._tx_lock.release()

    def _rx_loop(self):
        self.rx.settimeout(0.5)
        while not self._stop.is_set():
            try:
                fr = self.rx.recv(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            t = time.monotonic_ns()
            s = self.parse_sync(fr)
            if s:
                with self._lock:
                    self._on_sync(s[0], s[1], t)
            else:
                self._rxq.append(fr)

    # ---------------------------------------------------------------- I/O

    def send(self, payload):
        """Place one canonical packet in an upcoming bucket. Blocks until sent.

        Returns the canonical DATA actually transmitted (header || payload) --
        the exact bytes the certificate's `overall` covers, which a caller must
        retain if it wants to bind a later challenge to this packet."""
        if self.decl_shift is None:
            raise RuntimeError("send() before bootstrap()")
        if len(payload) > PLD_MAX:
            raise ValueError("payload %d > %d" % (len(payload), PLD_MAX))
        with self._tx_lock:
            return self._send_locked(payload)

    def _send_locked(self, payload):
        offset_ns = SEND_FRAC * self.period_ns
        with self._lock:                     # plan against a coherent flywheel snapshot
            now = time.monotonic_ns()
            k = self.cur_bkt
            while self._edge_of(k) + offset_ns - self.corr_ns < now + self.lead_ns:
                k += 1
            target = self._edge_of(k) + offset_ns - self.corr_ns
        while time.monotonic_ns() < target:  # spin to the placement instant
            pass
        ident = self.next_id
        self.next_id += 2
        declared = k + self.decl_shift
        fr = self._frame(payload, declared, ident)
        self.tx.send(fr)
        self._last_tx_ns = time.monotonic_ns()
        # The sync closing `declared` reports FIRST_ARR iff the device accepted this
        # frame, which is how bootstrap() (and any caller) can confirm placement.
        data = fr[14:]                       # canonical DATA: header || payload
        with self._lock:
            self._probe_pending[declared] = time.monotonic_ns()
            self._sent_unconfirmed[declared] = data   # promoted to sent_log on FIRST_ARR
        return data

    def _try_once(self, payload):
        """One send; True if the device confirms it accepted the frame."""
        data = self.send(payload)
        declared = struct.unpack("!I", data[4:8])[0]
        deadline = time.monotonic_ns() + int(6 * self.period_ns) + 5_000_000
        while time.monotonic_ns() < deadline:
            with self._lock:
                if declared in self._confirmed:
                    return True
            time.sleep(0.0005)
        return False

    def send_confirmed(self, payload, attempts=10):
        """Send, retrying until the device confirms it accepted the frame.

        The canonical protocol has no ack: the only delivery signal is FIRST_ARR on
        the sync that closes the bucket a frame declared. Placement is not perfect --
        a frame whose timing slips lands in the wrong bucket and is dropped in
        silence -- which is survivable for a chat token stream but not for a
        request/result exchange where losing one message stalls the whole round.
        Retrying on that signal is what makes the in-band control channel usable.

        Returns the canonical DATA that was accepted, so the caller still gets the
        exact committed bytes."""
        for attempt in range(attempts):
            data = self.send(payload)
            declared = struct.unpack("!I", data[4:8])[0]
            deadline = time.monotonic_ns() + int(6 * self.period_ns) + 5_000_000
            while time.monotonic_ns() < deadline:
                with self._lock:
                    if declared in self._confirmed:
                        return data
                time.sleep(0.0005)
            # Half the budget spent with nothing accepted means placement has latched
            # rather than that we are unlucky. Re-acquire once, mid-run, and spend the
            # rest of the attempts on a servo that can actually succeed.
            if attempt == attempts // 2:
                try:
                    self.recover()
                except Exception as e:                   # recovery is best-effort
                    self._log("recover failed: %s: %s" % (type(e).__name__, e))
        raise RuntimeError(
            "send_confirmed: device accepted none of %d attempts (cur_bkt=%s "
            "corr=%.1fus flywheel_alive=%s last_confirmed=%.1fs ago pending=%d) -- "
            "placement is failing, re-check the bucket lock"
            % (attempts, self.cur_bkt, self.corr_ns / 1e3,
               self._thread is not None and self._thread.is_alive(),
               (time.monotonic_ns() - self._last_confirmed_ns) / 1e9,
               len(self._probe_pending)))

    def recover(self):
        """Break a latched servo: drop the accumulated correction and re-bootstrap.

        Saturation is self-sustaining. The correction walks the frame out of the bucket
        it declares, the device drops it in silence, and that drop denies the servo the
        FIRST_ARR it would need to correct itself -- so the port stays deaf until the
        process restarts. Zeroing corr_ns puts placement back at the nominal mid-bucket
        point, which is where bootstrap() succeeded from in the first place; the servo
        then re-converges from a state that is known to get frames accepted.

        Two tiers, because there are two things that can be wrong and only one of them
        is the correction:

          1. Zero corr_ns and re-bootstrap. Nominal mid-bucket placement is where
             bootstrap() succeeded originally, so if the wound-up correction was the
             whole fault, frames start landing again immediately.
          2. If the re-bootstrap ALSO fails, the correction was not the fault: placement
             is wrong at every correction, which points at the period estimate the
             flywheel is running on. Re-measure it (relock()) and bootstrap once more.

        Tier 2 exists because tier 1 was observed to fail in exactly that way -- the
        port stayed deaf through a recover(), and restarting the process fixed it in
        four seconds. The only material difference between the two is that a restart
        re-runs lock(). This does that in place instead."""
        with self._lock:
            was = self.corr_ns
            self.corr_ns = 0.0
            self._probe_pending = {}
            self._probe_hits = 0
            self._recoveries += 1
        self._log("recover #%d: dropping servo correction (was %+.1fus), re-bootstrapping"
                  % (self._recoveries, was / 1e3))
        try:
            return self.bootstrap()
        except RuntimeError as e:
            self._log("recover: still rejected at nominal placement (%s)" % e)
        self.relock()
        with self._lock:
            self.corr_ns = 0.0          # the re-measure moved the phase under it
            self._probe_pending = {}
            self._probe_hits = 0
        return self.bootstrap()

    def audit_snapshot(self):
        """(confirmed, unconfirmed) copies for a byte-audit, taken atomically.

        The flywheel thread promotes frames between these two logs on every sync,
        so auditing them in place races: at best the recomputed root reflects a
        state that never existed, at worst the dict changes size mid-iteration and
        the audit raises. A shallow copy of each is enough -- the per-bucket lists
        are appended to only under the same lock, and the audit does not mutate."""
        with self._lock:
            return ({k: list(v) for k, v in self.sent_log.items()},
                    {k: list(v) for k, v in self.unconfirmed_log.items()})

    def recv(self, timeout=None):
        """Payload of the next non-sync canonical frame, or None on timeout.
        Sync frames keep feeding the flywheel while we wait."""
        t_end = None if timeout is None else time.monotonic_ns() + timeout * 1e9
        while True:
            while self._rxq:                       # drained by the flywheel thread
                out = self._strip(self._rxq.popleft())
                if out is not None:
                    return out
            if t_end is not None and time.monotonic_ns() >= t_end:
                return None
            time.sleep(0.002)

    def _strip(self, fr):
        """Canonical DATA (header+payload) of a forwarded frame, or None."""
        if len(fr) < 14 + HDR_BYTES:
            return None
        if self.accept_dst is not None and fr[0:6] != self.accept_dst:
            return None
        if self.accept_src is not None and fr[6:12] != self.accept_src:
            return None
        lt = int.from_bytes(fr[12:14], "big")
        if lt < HDR_BYTES or lt > 1500 or len(fr) < 14 + lt:
            return None
        return fr[14:14 + lt]


def open_port(iface, *, req=False, tuned=True, verbose=True,
               accept_dst=None, accept_src=None):
    """Locked, bootstrapped CanonPort ready to send."""
    p = CanonPort(iface, req=req, tuned=tuned, verbose=verbose,
                  accept_dst=accept_dst, accept_src=accept_src)
    p.lock()        # drives the socket directly; must precede the flywheel thread
    p.start()
    p.bootstrap()
    return p


if __name__ == "__main__":                       # smoke test: lock + bootstrap only
    iface = sys.argv[1] if len(sys.argv) > 1 else "eth0"
    port = open_port(iface, req="--req" in sys.argv)
    print("# ready: shift=+%d period=%.4f ms"
          % (port.decl_shift, port.period_ns / 1e6), flush=True)
