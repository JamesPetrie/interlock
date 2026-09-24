"""Shared cocotb helpers for the VPK180 shim benches (tb/test_axis_*,
tb/test_mac_port_shim): AXI-Stream and CoreTSE-bundle sources and sinks.

Byte order everywhere is little-endian per beat: the first wire byte of a
beat sits in bits [7:0] (MRMAC and CoreTSE convention)."""
import random

from cocotb.triggers import RisingEdge, ReadOnly


# ---------------------------------------------------------------- packing --
def pack_beats(data: bytes, nbytes: int):
    """bytes -> [(word, keep, last)] beats. An empty packet is one null beat."""
    n = max(1, (len(data) + nbytes - 1) // nbytes)
    beats = []
    for i in range(n):
        chunk = data[i * nbytes:(i + 1) * nbytes]
        word = int.from_bytes(chunk.ljust(nbytes, b"\0"), "little")
        keep = (1 << len(chunk)) - 1
        beats.append((word, keep, i == n - 1))
    return beats


def unpack_beat(word: int, keep: int, nbytes: int, check_zero_pad=False) -> bytes:
    assert keep == (1 << bin(keep).count("1")) - 1, f"tkeep not contiguous: {keep:#x}"
    raw = word.to_bytes(nbytes, "little")
    out = bytearray()
    for k in range(nbytes):
        if (keep >> k) & 1:
            out.append(raw[k])
        elif check_zero_pad:
            assert raw[k] == 0, f"unused lane {k} not zero"
    return bytes(out)


def tse_words(pkt: bytes):
    """bytes -> [(dat, sof, eof, bytevalid)] CoreTSE words (bytevalid = number
    of INVALID lanes, last word only)."""
    n = (len(pkt) + 3) // 4
    words = []
    for i in range(n):
        chunk = pkt[4 * i:4 * i + 4]
        dat = int.from_bytes(chunk.ljust(4, b"\0"), "little")
        last = i == n - 1
        words.append((dat, i == 0, last, (4 - len(chunk)) if last else 0))
    return words


async def wait_for(pred, clk, max_cycles=200_000, what="condition"):
    for _ in range(max_cycles):
        if pred():
            return
        await RisingEdge(clk)
    raise AssertionError(f"timeout waiting for {what}")


def rand_pkt(rng, lo=1, hi=200) -> bytes:
    return bytes(rng.getrandbits(8) for _ in range(rng.randint(lo, hi)))


# ------------------------------------------------------------- AXI-Stream --
class AxisSource:
    """Drives tvalid/tdata/tkeep/tlast(/tuser). tready=None means the sink
    cannot back-pressure (MRMAC RX style): every beat is taken in one cycle."""

    def __init__(self, clk, tvalid, tready, tdata, tkeep, tlast, tuser=None,
                 nbytes=8, gap=0.0, rng=None):
        self.clk, self.tvalid, self.tready = clk, tvalid, tready
        self.tdata, self.tkeep, self.tlast, self.tuser = tdata, tkeep, tlast, tuser
        self.nbytes, self.gap = nbytes, gap
        self.rng = rng or random.Random(1)
        self.tvalid.value = 0
        self.tdata.value = 0
        self.tkeep.value = 0
        self.tlast.value = 0
        if self.tuser is not None:
            self.tuser.value = 0

    async def send(self, pkt: bytes, err=False, null_last=False):
        beats = pack_beats(pkt, self.nbytes)
        if null_last:
            assert pkt and len(pkt) % self.nbytes == 0, "null_last needs a whole number of full beats"
            beats = [(w, k, False) for (w, k, _) in beats] + [(0, 0, True)]
        i = 0
        while i < len(beats):
            if self.gap and self.rng.random() < self.gap:
                self.tvalid.value = 0
                await RisingEdge(self.clk)
                continue
            w, k, last = beats[i]
            self.tvalid.value = 1
            self.tdata.value = w
            self.tkeep.value = k
            self.tlast.value = int(last)
            if self.tuser is not None:
                self.tuser.value = int(err and last)
            await ReadOnly()
            rdy = 1 if self.tready is None else int(self.tready.value)
            await RisingEdge(self.clk)
            if rdy:
                i += 1
        self.tvalid.value = 0
        self.tlast.value = 0
        if self.tuser is not None:
            self.tuser.value = 0


class AxisSink:
    """Collects packets. Drives tready (random stalls; `enabled=False` holds
    it low) unless tready=None. check_hold asserts tvalid never drops
    mid-packet (the MRMAC TX rule)."""

    def __init__(self, clk, tvalid, tready, tdata, tkeep, tlast, tuser=None,
                 nbytes=8, stall=0.0, rng=None, check_hold=False, check_zero_pad=False):
        self.clk, self.tvalid, self.tready = clk, tvalid, tready
        self.tdata, self.tkeep, self.tlast, self.tuser = tdata, tkeep, tlast, tuser
        self.nbytes, self.stall = nbytes, stall
        self.rng = rng or random.Random(2)
        self.check_hold, self.check_zero_pad = check_hold, check_zero_pad
        self.enabled = True
        self.packets, self.users = [], []
        self.null_last_beats = 0
        self.beats = 0
        self._cur, self._in_pkt = bytearray(), False
        if self.tready is not None:
            self.tready.value = 0

    async def run(self):
        while True:
            if self.tready is not None:
                self.tready.value = 1 if (self.enabled and self.rng.random() >= self.stall) else 0
            await ReadOnly()
            v = int(self.tvalid.value)
            r = 1 if self.tready is None else int(self.tready.value)
            if self.check_hold and self._in_pkt:
                assert v == 1, "tvalid dropped mid-packet"
            if v and r:
                self.beats += 1
                w, k, last = int(self.tdata.value), int(self.tkeep.value), int(self.tlast.value)
                u = int(self.tuser.value) if self.tuser is not None else 0
                if k == 0:
                    assert last, "null beat without tlast"
                    self.null_last_beats += 1
                else:
                    if not last:
                        assert k == (1 << self.nbytes) - 1, f"partial tkeep {k:#x} on a non-last beat"
                    self._cur += unpack_beat(w, k, self.nbytes, self.check_zero_pad)
                if last:
                    self.packets.append(bytes(self._cur))
                    self.users.append(u)
                    self._cur, self._in_pkt = bytearray(), False
                else:
                    self._in_pkt = True
            await RisingEdge(self.clk)


# --------------------------------------------------------- CoreTSE bundle --
class TseSource:
    """Drives a MAC-TX-shaped bundle (rdy/sof/eof/dat/bytevalid), waits on acpt."""

    def __init__(self, clk, rdy, acpt, sof, eof, dat, bytevalid, gap=0.0, rng=None):
        self.clk, self.rdy, self.acpt = clk, rdy, acpt
        self.sof, self.eof, self.dat, self.bv = sof, eof, dat, bytevalid
        self.gap, self.rng = gap, (rng or random.Random(3))
        self.rdy.value = 0
        self.sof.value = 0
        self.eof.value = 0
        self.dat.value = 0
        self.bv.value = 0

    async def send(self, pkt: bytes):
        words = tse_words(pkt)
        i = 0
        while i < len(words):
            if self.gap and self.rng.random() < self.gap:
                self.rdy.value = 0
                await RisingEdge(self.clk)
                continue
            dat, sof, eof, bv = words[i]
            self.rdy.value = 1
            self.dat.value = dat
            self.sof.value = int(sof)
            self.eof.value = int(eof)
            self.bv.value = bv
            await ReadOnly()
            acpt = int(self.acpt.value)
            await RisingEdge(self.clk)
            if acpt:
                i += 1
        self.rdy.value = 0
        self.sof.value = 0
        self.eof.value = 0


class TseSink:
    """Consumes a MAC-RX-shaped bundle, driving acpt with random stalls, and
    checks the bundle protocol (SOF only on the first word, BYTEVALID only on
    the EOF word)."""

    def __init__(self, clk, rdy, acpt, sof, eof, dat, bytevalid, stall=0.0, rng=None):
        self.clk, self.rdy, self.acpt = clk, rdy, acpt
        self.sof, self.eof, self.dat, self.bv = sof, eof, dat, bytevalid
        self.stall, self.rng = stall, (rng or random.Random(4))
        self.enabled = True
        self.packets = []
        self._cur, self._in_pkt = bytearray(), False
        self.acpt.value = 0

    async def run(self):
        while True:
            self.acpt.value = 1 if (self.enabled and self.rng.random() >= self.stall) else 0
            await ReadOnly()
            if int(self.rdy.value) and int(self.acpt.value):
                dat, sof, eof, bv = (int(self.dat.value), int(self.sof.value),
                                     int(self.eof.value), int(self.bv.value))
                assert sof == (not self._in_pkt), f"SOF={sof} but in_pkt={self._in_pkt}"
                assert eof or bv == 0, "BYTEVALID set on a non-EOF word"
                self._cur += dat.to_bytes(4, "little")[:4 - bv]
                if eof:
                    self.packets.append(bytes(self._cur))
                    self._cur, self._in_pkt = bytearray(), False
                else:
                    self._in_pkt = True
            await RisingEdge(self.clk)


class PulseCounter:
    """Counts cycles in which `sig` is high."""

    def __init__(self, clk, sig):
        self.clk, self.sig, self.count = clk, sig, 0

    async def run(self):
        while True:
            await ReadOnly()
            if int(self.sig.value):
                self.count += 1
            await RisingEdge(self.clk)


async def reset(dut, clk, rst_n, cycles=5):
    from cocotb.triggers import ClockCycles
    rst_n.value = 0
    await ClockCycles(clk, cycles)
    rst_n.value = 1
    await ClockCycles(clk, 2)
