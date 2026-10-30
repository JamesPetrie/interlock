"""The pacer: sends buckets at the right time, nothing else.

Which packet goes into which bucket and slot is decided by the dispatcher
(the bucket builder) in the main process. The pacer receives whole buckets,
already stamped and logged, and transmits each one at its instant: it owns
the wire and the clock, sleeps until shortly before a bucket's transmit
time and spins for the rest, paces the bucket's packets onto the wire,
reads the wire and timestamps syncs the moment they arrive. A packet it
cannot send inside its bucket, for whatever reason, it drops, as the
interlock would have, and says so; a packet whose send returns only after
its bucket closed (a stall inside the send) may or may not have made it,
and it reports that one as a suspect (a workaround for VM host stalls, see
`Dispatcher`). The dispatcher learns of every other
loss the way the protocol provides, from the syncs and the certificates,
and the node re-issues what got no answer. It runs as its own process,
pinned to a core where the OS allows, so nothing else delays it.

The two processes talk over a socketpair in framed messages:

    main -> pacer   'B' bucket n (len raw)*n       a bucket to send: its number and its packets in slot order
    pacer -> main   'D' (id)*n                     packets the pacer dropped
                    'S' (id)*n                     suspects: sent, but the send returned after the bucket closed
                    'R' raw                        a packet received (traffic, certificate)
                    'Y' raw next when              a sync received, the next bucket a packet handed over now can make,
                                                   and the pacer's monotonic time when it said so (shared with the main process)
                    'C' json                       clock statistics
"""
from __future__ import annotations

import json
import os
import select
import socket
import struct
import sys
import time
from collections import deque

from . import packet as P
from .clock import SyncClock
from .links import WIRE_BYTES, open_link
from .runtime import pin

SPIN = 50e-6                   # busy-wait this long before the transmit instant: short, so that a real-time
                               # pacer sending full buckets stays under the kernel's 95 % real-time budget
STATS_EVERY = 1.0
ROOM = 100e-6                  # housekeeping between buckets needs this much time before the next transmit instant
CHUNK = 160 << 10              # bytes taken from the main process per read: about one full bucket
EVENT = 30e-6                  # a bucket starting, or a packet leaving, later than this is reported as an event


# --- framing -------------------------------------------------------------------

def frame(kind: bytes, body: bytes) -> bytes:
    return kind + struct.pack("!I", len(body)) + body


def encode_bucket(bucket: int, raws: list[bytes]) -> bytes:
    return frame(b"B", struct.pack("!IH", bucket, len(raws)) + b"".join(struct.pack("!I", len(r)) + r for r in raws))


def decode_bucket(body: bytes) -> tuple[int, list[bytes]]:
    bucket, n = struct.unpack_from("!IH", body)
    off, raws = 6, []
    for _ in range(n):
        (ln,) = struct.unpack_from("!I", body, off)
        raws.append(body[off + 4:off + 4 + ln])
        off += 4 + ln
    return bucket, raws


def encode_ids(kind: bytes, ids: list[P.Id]) -> bytes:
    return frame(kind, b"".join(i.pack().to_bytes(8, "big") for i in ids))


def decode_ids(body: bytes) -> list[P.Id]:
    return [P.Id.unpack(int.from_bytes(body[i:i + 8], "big")) for i in range(0, len(body), 8)]


def id_of(raw: bytes) -> P.Id:
    """A traffic packet's ID from its header bytes."""
    dir, bucket, slot = struct.unpack_from("!HIH", raw, 8)
    return P.Id(dir, bucket, slot)


def fitting(clock, bucket: int, start: float, n: int, wire: float) -> int:
    """How many of `n` packets leaving back to back from `start`, a wire
    time apart, still land inside the bucket."""
    while n and not clock.open(bucket, start + (n - 1) * wire):
        n -= 1
    return n


class Framer:
    """Splits a byte stream into frames (kind, body). Bodies are views into
    the chunks as received: no copy per frame, and no shuffling of what is
    left in a buffer, so a backlog of large frames costs no more than reading
    it. Only a frame cut by a read boundary is joined (one copy, that frame)."""

    def __init__(self):
        self.rest = b""

    def feed(self, data: bytes) -> list[tuple[bytes, memoryview]]:
        if self.rest:
            data = self.rest + data
            self.rest = b""
        view = memoryview(data)
        out, off, n = [], 0, len(data)
        while n - off >= 5:
            (ln,) = struct.unpack_from("!I", data, off + 1)
            if n - off < 5 + ln:
                break
            out.append((bytes(view[off:off + 1]), view[off + 5:off + 5 + ln]))
            off += 5 + ln
        if off < n:
            self.rest = bytes(view[off:])
        return out


# --- the pacer process -----------------------------------------------------------

def run_pacer(conn: socket.socket, host: str, port: int, params: P.Params, direction: int,
              core: int | None, spin: float = SPIN, target: float = 0.25,
              link_mbps: float = 0.0, delta_unit: float = 1e-6, fill: float = 0.85, paced: bool = False) -> None:
    """The pacer process. `conn` is its end of the socketpair to the main
    process; `spin` how long before a transmit instant it stops sleeping and
    busy-waits (longer defeats timer coalescing, at the cost of the core);
    `link_mbps` the rate the receiver can take: a burst sends only what
    lands inside the bucket at that wire time, and with `paced` the packets
    of a bucket are spaced at it instead of handed over as one burst (a 25 G
    link into a 1 G device needs this); `delta_unit` the interlock's timer
    unit in seconds (10 ns on the FPGA).

    Between two buckets the loop does its housekeeping (taking buckets from
    the main process, the statistics) only when the next transmit instant
    is at least `ROOM` away, in bounded pieces, so that nothing it does can
    make a bucket start late."""
    note = pin(core)
    link = open_link(host, port, direction)
    wire = WIRE_BYTES * 8 / link_mbps / 1e6 if link_mbps else 0.0  # one packet on the wire, seconds
    pace = wire if paced else 0.0
    conn.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 22)
    conn.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 22)
    conn.setblocking(False)
    out = bytearray()                                              # to the main process, flushed as it drains

    def to_main(data: bytes) -> None:
        out.extend(data)
        flush(conn, out)

    def flush(sk: socket.socket, buf: bytearray) -> None:
        while buf:
            try:
                n = sk.send(buf)
            except BlockingIOError:
                return
            del buf[:n]

    clock = SyncClock(params.bucket_ms, target=target, delta_unit=delta_unit, fill=fill)
    buckets: deque[tuple[int, list[bytes]]] = deque()
    pending: deque[bytes] = deque()                                # packets off the wire, not handled yet
    dropped = 0
    cmds = Framer()
    trace = _trace_file()                                          # per-packet timestamps, for diagnosis
    errs: deque[float] = deque(maxlen=200)                         # actual minus scheduled transmit time, seconds
    per_packet: deque[float] = deque(maxlen=200)                   # seconds per packet within a bucket
    events: deque[dict] = deque(maxlen=64)                         # what went wrong since the last statistics
    work = {"link": 0.0, "conn": 0.0, "stats": 0.0}                # the longest piece of housekeeping of each kind, seconds
    sends = [0.0]                                                  # the longest send call since the last statistics
    to_main(frame(b"C", json.dumps({"pacer": note, "pace_us": round(pace * 1e6, 1)}).encode()))
    last_stats = time.monotonic()

    def transmit(bucket: int, raws: list[bytes], due: float) -> None:
        """The bucket's packets in slot order: spaced at the link's wire
        time when paced, otherwise as one write that the kernel serializes
        back to back. What would no longer land inside the bucket is dropped;
        what was handed over but returned only after the bucket closed is
        reported as a suspect."""
        nonlocal dropped
        t = time.monotonic()
        errs.append(t - due)                                       # how late the bucket starts leaving
        if t - due > EVENT:
            events.append({"what": "late start", "bucket": bucket, "us": round((t - due) * 1e6)})
        sent, gone, chunks, chunk_ids, suspects, send_took = 0, [], [], [], [], 0.0
        for id, raw in zip(map(id_of, raws), raws):
            if gone or not clock.open(bucket, t + sent * wire):    # the bucket runs out before it leaves: the rest are dropped
                gone.append(id)
                continue
            if pace:
                tk = t + sent * pace
                while time.monotonic() < tk:
                    pass
                ts = time.monotonic()
                if ts > tk + pace:                                 # fell behind (a stall): shift the schedule, never catch up
                    if ts - tk > EVENT:                            # by sending closer than the wire time allows
                        events.append({"what": "stall", "bucket": bucket, "slot": id.slot, "us": round((ts - tk) * 1e6),
                                       "send_us": round(send_took * 1e6)})   # how much of it was the previous send call
                    t += ts - tk
                    if not clock.open(bucket):                     # the stall ran the bucket out: dropped, not sent late
                        gone.append(id)
                        continue
                link.send(raw)
                send_took = time.monotonic() - ts
                if clock.late(bucket):                             # the send returned after the bucket closed:
                    suspects.append(id)                            # the interlock may or may not have taken it
                if send_took > sends[0]:
                    sends[0] = send_took
                clock.on_sent((bucket, id.slot), ts)
                if trace:
                    print(f"TRACE send {id.pack()} {ts:.6f} {(ts - due) * 1e6:.0f}", file=trace)
            else:
                chunks.append(raw)
                chunk_ids.append(id)
                clock.on_sent((bucket, id.slot), t)
            sent += 1
        if chunks:                                                 # the burst leaves now: what would land past the fill limit
            fit = fitting(clock, bucket, time.monotonic(), len(chunks), wire)   # is dropped, not sent late (a stall since
            gone[:0] = chunk_ids[fit:]                             # the checks above)
            sent -= len(chunks) - fit
            del chunks[fit:], chunk_ids[fit:]
        if chunks:
            link.send_many(chunks)
            if clock.late(bucket):
                suspects.extend(chunk_ids)
        if suspects:
            events.append({"what": "suspect", "bucket": bucket, "slot": suspects[0].slot, "n": len(suspects),
                           "us": round((time.monotonic() - due) * 1e6)})
            to_main(encode_ids(b"S", suspects))
        if sent > 1:
            per_packet.append((time.monotonic() - t) / sent)
        if gone:
            dropped += len(gone)
            events.append({"what": "drop", "bucket": bucket, "slot": gone[0].slot, "n": len(gone),
                           "us": round((time.monotonic() - due) * 1e6)})
            to_main(encode_ids(b"D", gone))

    final = min(spin, ROOM)                                        # the last stretch before a transmit instant: pure spin

    while True:
        due = clock.deadline(buckets[0][0]) if buckets and clock.synced else None
        now = time.monotonic()
        if due is not None and due - now <= final:
            while time.monotonic() < due:
                pass
            bucket, raws = buckets.popleft()
            if clock.late(bucket):                                 # too late for the whole bucket: dropped
                dropped += len(raws)
                events.append({"what": "late bucket", "bucket": bucket, "n": len(raws),
                               "us": round((time.monotonic() - due) * 1e6)})
                to_main(encode_ids(b"D", [id_of(r) for r in raws]))
            else:
                transmit(bucket, raws, due)
            continue
        until = 0.5 if due is None else min(0.5, due - now)        # time to the next transmit instant
        room = due is None or until >= ROOM                        # time for housekeeping before it
        timeout = 0.0 if pending or until <= spin else until - spin   # sleep until the spin window, or poll
        want = ([conn] if out else []) + ([link] if link.pending else [])
        readable, writable, _ = select.select([link, conn] if room else [link], want, [], max(0.0, timeout))
        if conn in writable:
            flush(conn, out)
        if link in writable:
            link.flush()
        if link in readable:
            packets = link.recv_packets()
            if packets is None:
                return                                             # the interlock closed the wire: nothing more to do
            pending.extend(packets)
        if pending:                                                # handled until the next transmit instant is near,
            t = time.monotonic()                                   # relayed in one write: a burst of syncs cannot stall a bucket
            limit = None if due is None else due - final
            while pending and (limit is None or time.monotonic() < limit):
                raw = pending.popleft()
                try:
                    pkt = P.parse(raw)
                except P.FormatError:
                    out.extend(frame(b"R", raw))
                    continue
                if isinstance(pkt, P.Sync):
                    if pkt.dir == direction:
                        clock.on_sync(pkt)
                        out.extend(frame(b"Y", raw + struct.pack("!Id", clock.bucket(), time.monotonic())))
                else:
                    out.extend(frame(b"R", raw))
            flush(conn, out)
            work["link"] = max(work["link"], time.monotonic() - t)
        if conn in readable:
            t = time.monotonic()
            data = conn.recv(CHUNK)                                # about one bucket at a time
            if not data:
                return                                             # the main process is gone
            for kind, body in cmds.feed(data):
                if kind == b"B":
                    buckets.append(decode_bucket(body))
            work["conn"] = max(work["conn"], time.monotonic() - t)
        if room and time.monotonic() - last_stats >= STATS_EVERY and (due is None or due - time.monotonic() >= ROOM):
            t = last_stats = time.monotonic()
            st = clock.stats()
            st["queued_buckets"] = len(buckets)
            st["dropped"] = dropped
            st["next_bucket"] = buckets[0][0] if buckets else None
            st["current"] = clock.interlock_bucket() if clock.synced else None
            landed = sorted(clock.landed)
            if landed:
                st["landed_p90"] = round(landed[min(len(landed) - 1, int(0.9 * len(landed)))], 3)
            if per_packet:
                st["packet_us"] = round(sorted(per_packet)[len(per_packet) // 2] * 1e6, 1)
            if errs:
                st["send_err_us_mean"] = round(sum(errs) / len(errs) * 1e6)
                st["send_err_us_max"] = round(max(errs) * 1e6)
            st["work_us"] = {k: round(v * 1e6) for k, v in work.items()}
            st["send_us_max"] = round(sends[0] * 1e6)
            sends[0] = 0.0
            for k in work:
                work[k] = 0.0
            if events:
                st["events"] = list(events)
                events.clear()
            to_main(frame(b"C", json.dumps(st).encode()))
            work["stats"] = time.monotonic() - t


def _trace_file():
    """ILSTACK_TRACE=<path>: trace lines go there (local disk, buffered);
    ILSTACK_TRACE=1 means stderr."""
    path = os.environ.get("ILSTACK_TRACE", "")
    if not path:
        return None
    return sys.stderr if path == "1" else open(path + f".{os.getpid()}", "a", buffering=1 << 16)
