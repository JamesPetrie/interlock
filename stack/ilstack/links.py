"""The links a sender puts packets on, outside the event loop: the TCP tunnel
to a software interlock, and the FPGA's raw 802.3 LENGTH frames. Both take a
whole bucket in one call; the raw link stamps departure times for a queue
that honours them (`fq`, `etf` offloaded, see `check_queue`), and asks for a
send buffer large enough to hold the buckets parked there.
"""
from __future__ import annotations

import os
import re
import socket
import struct
import subprocess
import time

from . import packet as P

WIRE_BYTES = 1538              # one full packet on an Ethernet wire: frame, preamble, gap
SO_TXTIME = SCM_TXTIME = 61    # Linux: a socket option and a control message carrying a departure time
CLOCK_MONOTONIC, CLOCK_TAI = 1, 11
SO_SNDBUFFORCE = 32            # Linux: a send buffer beyond wmem_max, for a privileged socket
MAC_FE = bytes([0x02, 0, 0, 0, 0, 0x01])      # the frontend's address on the raw link
MAC_CP = bytes([0x02, 0, 0, 0, 0, 0x02])      # compute's


FRAME_BYTES = 14 + P.FRAME_DATA                # the largest raw frame as a queue counts it: no preamble, FCS or gap
# the kernel release's queue: a quantum and first credit covering a bucket, room for the buckets parked ahead, and
# every frame of the socket one flow (fq hashes a raw socket's frames by their LENGTH field otherwise, so a shorter
# frame is a flow of its own and overtakes wherever the full frames' credit runs out); never maxrate (paces, reorders)
FQ = "fq quantum {q} initial_quantum {q} flow_limit {f} limit {l} orphan_mask 0 timer_slack 1us"
ETF = "etf clockid CLOCK_TAI delta 300000 offload"   # the hardware release's: the NIC holds a frame to its launch time


def check_queue(iface: str, release: str, slots: int, ahead: int) -> str | None:
    """Why the interface's root queue cannot release buckets of up to
    `slots` frames handed over `ahead` buckets early, with the `tc` command
    that fixes it; None if it can. `kernel` needs `fq`, which holds a frame
    to its stamp (CLOCK_MONOTONIC); `hardware` an `etf` offloaded to the NIC
    (CLOCK_TAI). The default `fq_codel` ignores departure times: frames
    handed over early would leave at once and be dropped."""
    try:
        out = subprocess.run(["tc", "qdisc", "show", "dev", iface], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    return queue_problem(out, iface, release, slots, ahead)


def queue_problem(tc_output: str, iface: str, release: str, slots: int, ahead: int) -> str | None:
    """`check_queue` from the text of `tc qdisc show dev <if>`."""
    m = re.search(r"^qdisc (\S+) \S+ (?:dev \S+ )?root\b(.*)$", tc_output, re.M)
    kind, opts = (m.group(1), m.group(2)) if m else ("missing", "")

    def value(key: str) -> int | None:                          # tc's sizes: 100000b, 128Kb, 1Mb; counts: 2000p
        v = re.search(rf"(?<!\w){key} (\d+)([KMG]?)", opts)
        return int(v.group(1)) * {"": 1, "K": 1 << 10, "M": 1 << 20, "G": 1 << 30}[v.group(2)] if v else None
    if release == "kernel":
        bucket, parked = slots * FRAME_BYTES, (ahead + 1) * slots   # the bucket on its way out and `ahead` behind it
        fix = FQ.format(q=max(100000, bucket), f=max(2000, parked), l=max(10000, parked))
        if kind != "fq":
            why = [f"the root queue is {kind}, not fq"]
        else:
            why = [f"{k} {value(k)} < {n} ({what})" for k, n, what in
                   (("quantum", bucket, "a bucket's bytes"), ("initial_quantum", bucket, "a bucket's bytes"),
                    ("flow_limit", parked, "frames parked ahead"), ("limit", parked, "frames parked ahead"))
                   if (value(k) or 0) < n]
            if value("orphan_mask") != 0:
                why.append(f"orphan_mask {value('orphan_mask')}, not 0 (a bucket's frames in one flow, in order)")
            if re.search(r"(?<!\w)maxrate ", opts):
                why.append("maxrate set (fq's pacing reorders)")
    elif release == "hardware":
        fix = ETF
        ok = kind == "etf" and re.search(r"(?<!\w)clockid TAI\b", opts) and re.search(r"(?<!\w)offload on\b", opts)
        why = [] if ok else [f"the root queue is {kind}, not etf with clockid TAI and offload on"]
    else:
        return None
    if not why:
        return None
    return f"{release} release on {iface}: {'; '.join(why)}. Set it with\n  tc qdisc replace dev {iface} root {fix}"



class PacketFramer:
    """Splits a byte stream into protocol packets."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self.buf += data
        out = []
        while len(self.buf) >= 8:
            n = P.frame_length(self.buf[:8])
            if len(self.buf) < n:
                break
            out.append(bytes(self.buf[:n]))
            del self.buf[:n]
        return out


class TcpLink:
    """The TCP tunnel: packets framed by their own length field, writes buffered.
    A whole bucket is one write."""

    def __init__(self, host: str, port: int):
        self.sock = _connect(host, port)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
        self.sock.setblocking(False)
        self.out = bytearray()
        self.framer = PacketFramer()

    def fileno(self) -> int:
        return self.sock.fileno()

    @property
    def pending(self) -> bool:
        return bool(self.out)

    def send(self, data: bytes) -> None:
        self.out.extend(data)
        self.flush()

    def send_many(self, packets: list[bytes], when: float | None = None) -> None:
        self.send(b"".join(packets))

    def departure_times(self) -> None:
        raise OSError("a TCP tunnel cannot stamp departure times")

    def flush(self) -> None:
        while self.out:
            try:
                n = self.sock.send(self.out)
            except BlockingIOError:
                return
            del self.out[:n]

    def recv_packets(self) -> list[bytes] | None:
        """Packets that arrived; None once the other end has closed."""
        data = self.sock.recv(1 << 20)
        if not data:
            return None
        return self.framer.feed(data)


class RawLink:
    """Raw 802.3 LENGTH frames on an interface, the FPGA's wire: destination
    and source are the two fixed addresses, LENGTH is the packet's length, the
    packet follows. Needs CAP_NET_RAW. One frame per read, our own
    transmissions skipped."""

    ETH = 14
    OUTGOING = 4

    def __init__(self, iface: str, direction: int):
        self.sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0003))
        self.sock.bind((iface, 0))
        try:                                                       # frames parked in the queue until their departure time
            self.sock.setsockopt(socket.SOL_SOCKET, SO_SNDBUFFORCE, 64 << 20)   # count against this: room for 400 buckets
        except OSError:                                            # not root: the capped buffer (a few buckets)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 22)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 22)
        self.sock.setblocking(False)
        self.head = (MAC_CP + MAC_FE) if direction == P.INBOUND else (MAC_FE + MAC_CP)
        self.pending = False

    def fileno(self) -> int:
        return self.sock.fileno()

    def send(self, packet: bytes) -> None:
        parts = [self.head, len(packet).to_bytes(2, "big"), packet]      # no copy of the packet
        while True:
            try:
                self.sock.sendmsg(parts)
                return
            except BlockingIOError:
                pass

    def departure_times(self, clockid: int = CLOCK_MONOTONIC) -> None:
        """Let the interface's queue release each frame at the time stamped
        on it (SO_TXTIME): CLOCK_MONOTONIC for `fq` (the kernel release),
        CLOCK_TAI for `etf` and a NIC's launch time (the hardware release)."""
        self.sock.setsockopt(socket.SOL_SOCKET, SO_TXTIME, struct.pack("@iI", clockid, 0))

    def send_many(self, packets: list[bytes], when: float | list[float] | None = None) -> None:
        """A whole bucket in one system call (sendmmsg). `when`: the
        departure time stamped on every frame, or one per frame, in seconds
        of the clock given to `departure_times`, for a queue that releases at
        those times (`fq`, `etf`). Without stamps the frames leave at once:
        fine on a link that serializes them at the receiver's rate (a 1 GbE
        link), never onto a 25 G link straight into a 1 G device, which needs
        a stamp per frame a wire time apart or `send` packet by packet."""
        global _sendmmsg
        if _sendmmsg is None:
            try:
                _sendmmsg = _Sendmmsg()
            except AttributeError:                                 # no sendmmsg on this platform: one call per packet
                _sendmmsg = False
        if not _sendmmsg:
            for p in packets:
                self.send(p)
            return
        head = self.head
        stamps = None if when is None else [int(w * 1e9) for w in when] if isinstance(when, list) else int(when * 1e9)
        _sendmmsg.send(self.sock, [head + len(p).to_bytes(2, "big") + p for p in packets], stamps)

    def flush(self) -> None:
        pass

    def recv_packets(self) -> list[bytes] | None:
        out = []
        while True:
            try:
                data, addr = self.sock.recvfrom(2048)
            except BlockingIOError:
                return out
            if addr[2] == self.OUTGOING or len(data) < self.ETH + P.HEADER_SIZE:
                continue
            length = int.from_bytes(data[12:14], "big")
            if length < P.HEADER_SIZE or length > 1500 or len(data) < self.ETH + length:
                continue
            out.append(data[self.ETH:self.ETH + length])


class _Sendmmsg:
    """sendmmsg(2) through libc, for a link that takes a whole bucket in one
    call. The frames are joined into one buffer (one copy of the bucket) and
    the iovec/mmsghdr arrays are reused, so a call costs one join and the
    system call, not a Python object per packet."""

    def __init__(self, capacity: int = 256):
        import ctypes

        class iovec(ctypes.Structure):
            _fields_ = [("iov_base", ctypes.c_void_p), ("iov_len", ctypes.c_size_t)]

        class msghdr(ctypes.Structure):
            _fields_ = [("msg_name", ctypes.c_void_p), ("msg_namelen", ctypes.c_uint32), ("msg_iov", ctypes.POINTER(iovec)),
                        ("msg_iovlen", ctypes.c_size_t), ("msg_control", ctypes.c_void_p), ("msg_controllen", ctypes.c_size_t),
                        ("msg_flags", ctypes.c_int)]

        class mmsghdr(ctypes.Structure):
            _fields_ = [("msg_hdr", msghdr), ("msg_len", ctypes.c_uint)]
        self.ctypes = ctypes
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.sendmmsg.restype = ctypes.c_int                  # AttributeError here where the platform has none
        self.iovs = (iovec * capacity)()
        self.msgs = (mmsghdr * capacity)()
        for i in range(capacity):
            self.msgs[i].msg_hdr.msg_iov = ctypes.pointer(self.iovs[i])
            self.msgs[i].msg_hdr.msg_iovlen = 1
        self.buf = bytearray()
        # one SCM_TXTIME control message per message (cmsghdr + u64 nanoseconds)
        self.cmsgs = (ctypes.c_char * (24 * capacity))()
        for i in range(capacity):
            struct.pack_into("@LiI", self.cmsgs, 24 * i, 24, socket.SOL_SOCKET, SCM_TXTIME)
        self.cmsg_base = ctypes.addressof(self.cmsgs)

    def send(self, sock: socket.socket, frames: list[bytes], when_ns: int | list[int] | None = None) -> None:
        """`when_ns`: the departure time (the sending clock's nanoseconds)
        stamped on every frame, or one per frame, for a queue that honours
        them (`fq`, `etf`)."""
        ctypes, n = self.ctypes, len(frames)
        if n > len(self.msgs):
            raise ValueError(f"more than {len(self.msgs)} frames in one call")
        self.buf = bytearray().join(frames)                        # one copy of the bucket, kept until the call returns
        base = ctypes.addressof((ctypes.c_char * len(self.buf)).from_buffer(self.buf))
        off, iovs, msgs = 0, self.iovs, self.msgs
        times = when_ns if isinstance(when_ns, list) else [when_ns] * n
        for i, f in enumerate(frames):
            iovs[i].iov_base, iovs[i].iov_len = base + off, len(f)
            if times[i] is None:
                msgs[i].msg_hdr.msg_control, msgs[i].msg_hdr.msg_controllen = None, 0
            else:
                struct.pack_into("@Q", self.cmsgs, 24 * i + 16, times[i])
                msgs[i].msg_hdr.msg_control, msgs[i].msg_hdr.msg_controllen = self.cmsg_base + 24 * i, 24
            off += len(f)
        done = 0
        while done < n:
            r = self.libc.sendmmsg(sock.fileno(), ctypes.byref(self.msgs[done]), n - done, 0)
            if r < 0:
                err = ctypes.get_errno()
                if err == 11:                                      # EAGAIN: the queue is full for a moment
                    continue
                raise OSError(err, os.strerror(err))
            done += r


_sendmmsg: _Sendmmsg | None = None


def open_link(host: str, port: int, direction: int):
    """`raw:<iface>` for the FPGA's wire, otherwise a TCP host."""
    if host.startswith("raw:"):
        return RawLink(host[4:], direction)
    return TcpLink(host, port)


def _connect(host: str, port: int, retries: int = 300, delay: float = 0.2) -> socket.socket:
    for attempt in range(retries):
        try:
            s = socket.create_connection((host, port))
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            return s
        except OSError:
            if attempt == retries - 1:
                raise
            time.sleep(delay)
    raise AssertionError("unreachable")
