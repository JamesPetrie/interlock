"""A timing test for one direction of the link, with nothing else in the way.

The stub stands where the interlock's port is: it reads, timestamps, decides
BUCKET equal or not, keeps the first and last slot of each bucket and where
they landed, and sends a sync every bucket, like the device would. No
hashing, no forwarding, no certificates: a few microseconds per packet on
one pinned core, so it keeps up with full buckets and its own delay stays
out of the measurement.

The sender is the real pacer, fed with full-size dummy buckets at a chosen
fill, for as many buckets as asked.
"""
from __future__ import annotations

import asyncio
import select
import socket
import struct
import sys
import time
from collections import Counter, defaultdict, deque

from . import packet as P
from .links import PacketFramer
from .runtime import pin

_HEAD = struct.Struct("!IIHIH")


def _pct(xs: list, p: float):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))]


def _summary(name: str, hist: Counter) -> str:
    """Percentiles of a histogram (value -> count): one walk over the distinct values."""
    n = sum(hist.values())
    if not n:
        return f"{name}: no data"
    marks, out, seen = [(.5, "p50"), (.9, "p90"), (.99, "p99"), (.999, "p999")], [], 0
    for v in sorted(hist):
        seen += hist[v]
        while marks and seen > int(marks[0][0] * n):
            out.append(f"{marks.pop(0)[1]}={v}")
    return f"{name}: n={n} " + " ".join(out) + f" max={max(hist)}"


class Stub:
    """The receiving side's view of one direction, bucket by bucket."""

    def __init__(self, params: P.Params, direction: int, t0: float):
        self.params, self.direction, self.t0 = params, direction, t0
        self.L = params.bucket_ms / 1000.0
        self.closed = -1
        self.open: dict[int, dict] = {}
        self.accepted = 0
        self.late = 0
        self.early = 0
        self.other = 0
        self.lateness: dict[int, int] = defaultdict(int)       # buckets late -> count
        self.counts: Counter = Counter()                        # histograms: value -> buckets; a report stays cheap
        self.first_landed: Counter = Counter()                  # us into the bucket
        self.last_landed: Counter = Counter()
        self.spans: Counter = Counter()

    def bucket_at(self, t: float) -> int:
        return int((t - self.t0) / self.L)

    def feed(self, raw: bytes, t: float) -> None:
        pld_len, type_, dir, bucket, slot = _HEAD.unpack_from(raw)
        if type_ != P.TYPE_TRAFFIC or dir != self.direction:
            self.other += 1
            return
        now = self.bucket_at(t)
        delta = int((t - self.t0 - now * self.L) * 1e6)
        if bucket != now:
            if bucket < now:
                self.late += 1
                self.lateness[now - bucket] += 1
            else:
                self.early += 1
            return
        b = self.open.get(now)
        if b is None:
            b = self.open[now] = {"first_slot": slot, "first": delta, "last_slot": slot, "last": delta, "n": 0}
        b["last_slot"], b["last"], b["n"] = slot, delta, b["n"] + 1
        self.accepted += 1

    def close(self, bucket: int) -> bytes:
        """Close a bucket: record it, return the sync packet for the sender."""
        self.closed = bucket
        b = self.open.pop(bucket, None)
        if b is None:
            return P.Sync(self.direction, bucket).pack()
        self.counts[b["n"]] += 1
        self.first_landed[b["first"]] += 1
        self.last_landed[b["last"]] += 1
        self.spans[b["last"] - b["first"]] += 1
        return P.Sync(self.direction, bucket, b["first_slot"], b["first"]).pack()

    def report(self) -> str:
        total = self.accepted + self.late + self.early
        lines = [f"buckets with traffic {sum(self.counts.values())} of {self.closed + 1}; packets {total}: accepted {self.accepted}, "
                 f"late {self.late} ({100 * self.late / max(1, total):.2f}%), early {self.early}, other {self.other}",
                 "  " + _summary("packets per bucket", self.counts),
                 "  " + _summary("first slot landed (us)", self.first_landed),
                 "  " + _summary("last slot landed (us)", self.last_landed),
                 "  " + _summary("first-to-last span (us)", self.spans)]
        if self.lateness:
            lines.append("  late by (buckets: count): " + ", ".join(f"{k}: {v}" for k, v in sorted(self.lateness.items())[:12]))
        return "\n".join(lines)


def run_stub(host: str, port: int, direction: int = P.INBOUND, bucket_ms: float = 1.0, core: int | None = None,
             trace: str | None = None, report_every: float = 5.0) -> None:
    """Listen, accept one sender at a time, timestamp and judge what arrives,
    send a sync per bucket, print a report every `report_every` seconds.
    `trace`: a file (local disk) that gets one line per packet."""
    params = P.Params(bucket_ms=bucket_ms)
    tf = open(trace, "a", buffering=1 << 16) if trace else None
    print("stub:", pin(core), flush=True)
    srv = socket.create_server((host, port), reuse_port=False)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.setblocking(False)
    stub = Stub(params, direction, time.monotonic())
    client: socket.socket | None = None
    framer = PacketFramer()
    out = bytearray()
    buf = bytearray(1 << 20)                                       # one receive buffer, touched once
    view = memoryview(buf)
    last_report = time.monotonic()
    while True:
        now = time.monotonic()
        while stub.closed + 1 < stub.bucket_at(now):                       # ticks: close every bucket that ended
            out += stub.close(stub.closed + 1)
        if client is None:
            r, _, _ = select.select([srv], [], [], 0.0005)
            if r:
                client, _ = srv.accept()
                client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                client.setblocking(False)
                framer = PacketFramer()
                out = bytearray()
                print("stub: sender connected", flush=True)
            continue
        r, w, _ = select.select([client], [client] if out else [], [], 0)
        if r:
            try:
                n = client.recv_into(view)
            except (BlockingIOError, InterruptedError):
                n = -1
            except OSError:
                n = 0
            if n == 0:
                print("stub: sender gone", flush=True)
                client.close()
                client = None
                continue
            if n > 0:
                t = time.monotonic()
                for raw in framer.feed(bytes(view[:n])):
                    stub.feed(raw, t)
                    if tf:
                        print(f"TRACE recv {int.from_bytes(raw[8:16], 'big')} {t:.6f} {stub.bucket_at(t)}", file=tf)
        if w and out:
            try:
                n = client.send(out)
                del out[:n]
            except (BlockingIOError, InterruptedError):
                pass
            except OSError:
                client.close()
                client = None
        if now - last_report >= report_every:
            last_report = now
            print(f"--- {time.strftime('%H:%M:%S')} bucket {stub.closed}", flush=True)
            print(stub.report(), flush=True)


async def run_send(host: str, port: int, params: P.Params, slots: int, buckets: int, target: float = 0.1,
                   core: int | None = None, spin: float = 50e-6, ahead: int = 1, link_mbps: float = 1000.0,
                   every: int = 1, delta_unit: float = 1e-6, fill: float = 0.85, ahead_msgs: int = 1,
                   release: str = "process", rate_limit: bool = False) -> dict:
    """Send `buckets` buckets of `slots` full-size packets, one message per
    bucket, `every` buckets apart, through the chosen release step.
    Returns the dispatcher's and the clock's statistics."""
    from .clock import SyncClock
    from .dispatcher import Dispatcher, ProcessDispatcher
    from .log import RingLog
    from .links import CLOCK_MONOTONIC, CLOCK_TAI, check_queue
    from .runtime import low_jitter
    from .verifier import audit, resolve
    from .wire import RawWire
    log = RingLog(keep=3 * params.bkt_num)
    if release == "process":
        d = ProcessDispatcher(params, (host, port), log, P.INBOUND, core=core, spin=spin,
                              max_slots=slots, target=target, link_mbps=link_mbps, ahead=ahead,
                              delta_unit=delta_unit, pace=rate_limit, fill=fill)
    elif release in ("kernel", "hardware"):
        if not host.startswith("raw:"):
            raise ValueError("kernel and hardware release need a raw link (--stub raw:<iface>)")
        problem = check_queue(host[4:], release, params.num_slot, ahead)
        if problem:
            raise SystemExit(problem)
        # this process hands over on a deadline: no GC pauses, and the log's memory faulted in now; faults while
        # it fills (the first seconds, ~90k of them) cost frames at the FPGA's ingress
        low_jitter(64 + (log.keep * slots * 2048 >> 20))
        hw = release == "hardware"                               # an offloaded etf's clock is CLOCK_TAI; fq's CLOCK_MONOTONIC
        wire = RawWire(host[4:], P.INBOUND, CLOCK_TAI if hw else CLOCK_MONOTONIC)
        offset = time.clock_gettime(time.CLOCK_TAI) - time.monotonic() if hw else 0.0
        d = Dispatcher(params, wire, SyncClock(params.bucket_ms, target=target, fill=fill, delta_unit=delta_unit),
                       log, P.INBOUND, max_slots=slots, link_mbps=link_mbps, release=release, ahead=ahead,
                       rate_limit=rate_limit, tai_offset=offset)
    else:
        raise ValueError(f"unknown release {release!r}")
    audits = {"certificates": 0, "matching": 0, "resolved": 0, "device": None}

    def on_certificate(cert):                                    # the interlock's commitment against our log, as it arrives
        audits["certificates"] += 1
        audits["device"] = cert.device
        if audit(params, log, cert, (P.INBOUND,))["inward"]:
            audits["matching"] += 1
        elif (suspects := d.suspects_in(cert)) and (gone := resolve(params, log, cert, suspects, (P.INBOUND,))) is not None:
            audits["matching"] += 1                              # it matches once the suspects it did not take are out
            audits["resolved"] += 1
            d.not_accepted(gone)
            print(f"certificate {cert.bkt_start}..{cert.bkt_start + cert.bkt_num - 1} matches without {len(gone)} "
                  f"of {len(suspects)} suspects", file=sys.stderr, flush=True)
        else:
            print(f"certificate {cert.bkt_start}..{cert.bkt_start + cert.bkt_num - 1} does not match the log "
                  f"(dropped so far {d.clock_stats().get('dropped')}, lost {d.stats.lost})", file=sys.stderr, flush=True)
    d.certificate_listeners.append(on_certificate)

    def on_event(event: dict) -> None:                           # the pacer's late starts, stalls and drops, as they happen
        print(f"{time.strftime('%H:%M:%S')} {sent_so_far[0]} buckets: {event}", file=sys.stderr, flush=True)
    sent_so_far = [0]
    d.event_listeners.append(on_event)
    await d.start()

    async def drain_lost():                                      # a timing run re-issues nothing: keep the queue from growing
        while True:
            await d.lost.get()
    drain = asyncio.create_task(drain_lost())
    while not d._ready():
        await asyncio.sleep(0.01)
    payload = bytes(params.s_max - params.s_max % params.token_unit)
    kc = bytes(range(32))
    t0 = time.monotonic()
    recent: deque = deque()
    for i in range(buckets):
        sent_so_far[0] = i
        recent.append(d.send([payload] * slots, kc, tag=i))
        while len(recent) > 8:
            d.forget(recent.popleft())
        await asyncio.sleep(0)                                   # after every bucket: the receiver reads, the writer writes
        # keep `ahead_msgs` buckets queued at the sender beyond the one it is on, no more
        while d.builder._next > d._now_bucket() + ahead_msgs:
            await asyncio.sleep(0.0002)
        if every > 1:
            await asyncio.sleep(params.bucket_ms * every / 1000.0)
        if i % 100000 == 99999:
            print(f"{i + 1} buckets, {time.strftime('%H:%M:%S')}: certificates {audits['certificates']} matching "
                  f"{audits['matching']}, lost {d.stats.lost} = {d.stats.lost / ((i + 1) * slots):.1e}, "
                  f"pacer {d.clock_stats()}", file=sys.stderr, flush=True)
    await asyncio.sleep(0.05)
    while recent:
        d.forget(recent.popleft())
    elapsed = time.monotonic() - t0
    await asyncio.sleep(1.2)                                     # the last syncs, and the pacer's statistics
    stats = {"buckets": buckets, "slots": slots, "seconds": round(elapsed, 2), "release": release, "rate_limit": rate_limit,
             "dispatcher": vars(d.stats),
             "builder": d.builder_stats(), "clock": d.clock_stats(),
             "certificates": audits["certificates"], "certificates_matching_log": audits["matching"],
             "certificates_resolved": audits["resolved"],
             "certificate_device": audits["device"]}
    drain.cancel()
    await d.stop()
    return stats


def run_recv(iface: str, bucket_ms: float, seconds: float, core: int | None = None) -> dict:
    """Count the traffic frames the interlock forwards onto `iface` (the far
    side of the link under test), per bucket, for `seconds`."""
    from .links import RawLink
    print("recv:", pin(core), flush=True)
    link = RawLink(iface, P.OUTBOUND)
    per_bucket: dict[int, set] = defaultdict(set)
    other = 0
    t_end = time.monotonic() + seconds
    while time.monotonic() < t_end:
        r, _, _ = select.select([link], [], [], 0.2)
        if not r:
            continue
        for raw in link.recv_packets() or []:
            _, type_, dir, bucket, slot = _HEAD.unpack_from(raw)
            if type_ == P.TYPE_TRAFFIC:
                per_bucket[bucket].add(slot)
            else:
                other += 1
    counts = [len(v) for v in per_bucket.values()]
    return {"frames": sum(counts), "buckets": len(per_bucket), "other": other,
            "first_bucket": min(per_bucket) if per_bucket else None, "last_bucket": max(per_bucket) if per_bucket else None,
            "per_bucket_p50": _pct(counts, .5), "per_bucket_max": max(counts) if counts else None}
