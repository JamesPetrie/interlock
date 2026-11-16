"""cocotb testbench for canon_proc.sv — the canonical-layer packet processor.

canon_proc sees only the canonical packet (Ethernet DATA, already de-framed
and word-aligned): a 12-byte header (id / pkt_idx / pld_len) + payload,
delimited by tlast. Packets stream through verbatim; the header checks
suppress failing packets entirely, while payload failures (length
cross-check, upstream truncation flag) are signalled as the drop flag on
tuser at tlast. tuser also carries the total byte length (header + pld_len)
on beat #0.

Sequence rules (ID bit 63 = inference flag, ignored for ordering):
  - ordering is on ID_cont (low 63 bits): a new packet needs
    ID_cont > previous ID_cont and PKT_IDX = 0;
  - a continuation (same ID_cont, PKT_IDX = previous + 1) is allowed only for
    an open inference request: the previous fragment was full-size
    (total = 1500) and inference; a shorter fragment, or a non-inference
    packet, cannot continue;
  - non-inference packets are always single-packet (PKT_IDX = 0) and ride
    their own increasing ID_cont, disjoint from inference; ID = 0 is reserved
    (it can never exceed the prior ID_cont) and is always suppressed;
  - every verified (non-dropped) packet advances the ordering state.

AXI-Stream: tdata[31:0] (byte 0 in [7:0]), tkeep[3:0] (contiguous from LSB),
tlast, tvalid/tready; slave tuser = truncation flag at tlast, master
tuser[15:0] = total length at beat #0 / drop flag (bit 0) at tlast.
"""
import random
from dataclasses import dataclass

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, Combine

CANON_HDR_BYTES = 12
PKT_MAX = 1500                       # canonical packet must fit one Eth DATA field
PLD_MAX = PKT_MAX - CANON_HDR_BYTES  # 1488; a full-size fragment carries exactly this
INF = 1 << 63                        # ID MSB: inference packet


@dataclass
class Pkt:
    can_id: int
    pkt_idx: int
    pld_len: int          # value written into the header field
    payload_len: int = -1 # actual payload bytes (-1: match pld_len)
    in_drop: bool = False # upstream truncation flag on the tlast beat
    seed: int = 0
    raw_len: int = 0      # if > 0: raw packet of this many bytes (runt tests)

    def data(self) -> bytes:
        rng = random.Random(self.seed)
        if self.raw_len:
            return bytes(rng.randint(0, 255) for _ in range(self.raw_len))
        plen = self.payload_len if self.payload_len >= 0 else self.pld_len
        payload = bytes(rng.randint(0, 255) for _ in range(plen))
        return (self.can_id.to_bytes(8, "big")
                + self.pkt_idx.to_bytes(2, "big")
                + self.pld_len.to_bytes(2, "big")
                + payload)


def model(pkts):
    """Mirror canon_proc's verdicts: list of expected (bytes, tuser0, drop)."""
    out = []
    prev_idc, prev_idx, prev_cont = 0, 0, False  # reset state
    for p in pkts:
        data = p.data()
        if len(data) <= CANON_HDR_BYTES:      # fractional header
            continue
        if not (1 <= p.pld_len <= PLD_MAX):   # pld_len range check
            continue
        inf = bool(p.can_id & INF)
        idc = p.can_id & (INF - 1)            # ID_cont (inf bit ignored)
        if idc > prev_idc:                    # new ID_cont
            seq_ok = p.pkt_idx == 0
        elif inf and prev_cont and idc == prev_idc:  # open inference continuation
            seq_ok = p.pkt_idx == ((prev_idx + 1) & 0xFFFF)
        else:
            seq_ok = False
        if not seq_ok:
            continue
        total = CANON_HDR_BYTES + p.pld_len
        drop = p.in_drop or (len(data) != total)
        out.append((data, total, drop))
        if not drop:                          # every verified packet advances
            prev_idc, prev_idx = idc, p.pkt_idx
            prev_cont = (len(data) == PKT_MAX) and inf
    return out


def pack_axis(data: bytes, in_drop: bool):
    """bytes -> list of (tdata, tkeep, tlast, tuser) beats."""
    n = (len(data) + 3) // 4
    beats = []
    for w in range(n):
        chunk = data[4 * w: 4 * w + 4]
        d = keep = 0
        for k, b in enumerate(chunk):
            d |= b << (8 * k)
            keep |= 1 << k
        last = int(w == n - 1)
        beats.append((d, keep, last, int(last and in_drop)))
    return beats


async def drive_axis(dut, pkt: Pkt):
    beats = pack_axis(pkt.data(), pkt.in_drop)
    i = 0
    while i < len(beats):
        d, keep, last, user = beats[i]
        dut.tvalid_s.value = 1
        dut.tdata_s.value = d
        dut.tkeep_s.value = keep
        dut.tlast_s.value = last
        dut.tuser_s.value = user
        await ReadOnly()
        ready = int(dut.tready_s.value)
        await RisingEdge(dut.clk)
        if ready:
            i += 1
    dut.tvalid_s.value = 0
    dut.tlast_s.value = 0
    dut.tkeep_s.value = 0
    dut.tuser_s.value = 0


async def recv_axis(dut, n_packets, throttle=0.0, rng=None):
    """Collect n_packets from the master port: (bytes, tuser@beat0, tuser@tlast)."""
    if rng is None:
        rng = random.Random(0)
    pkts = []
    cur = bytearray()
    tuser0 = None
    while len(pkts) < n_packets:
        dut.tready_m.value = 0 if rng.random() < throttle else 1
        await ReadOnly()
        if int(dut.tvalid_m.value) and int(dut.tready_m.value):
            d = int(dut.tdata_m.value)
            keep = int(dut.tkeep_m.value)
            last = int(dut.tlast_m.value)
            user = int(dut.tuser_m.value)
            if tuser0 is None:
                tuser0 = user
            for k in range(4):
                if (keep >> k) & 1:
                    cur.append((d >> (8 * k)) & 0xFF)
            if last:
                pkts.append((bytes(cur), tuser0, user))
                cur = bytearray()
                tuser0 = None
        await RisingEdge(dut.clk)
    dut.tready_m.value = 1
    return pkts


async def quiet_check(dut, cycles=40):
    """No further packets: tvalid must stay low (tready held high)."""
    dut.tready_m.value = 1
    for _ in range(cycles):
        await ReadOnly()
        assert int(dut.tvalid_m.value) == 0, "unexpected extra output beat"
        await RisingEdge(dut.clk)


async def reset(dut, period_ns=8):
    cocotb.start_soon(Clock(dut.clk, period_ns, unit="ns").start())
    dut.rst_n.value = 0
    dut.tvalid_s.value = 0
    dut.tdata_s.value = 0
    dut.tkeep_s.value = 0
    dut.tlast_s.value = 0
    dut.tuser_s.value = 0
    dut.tready_m.value = 0
    for _ in range(5):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    for _ in range(2):
        await RisingEdge(dut.clk)


async def forward(dut, pkts, throttle=0.0, rng=None):
    """Drive pkts, receive what the model predicts, then verify silence."""
    exp = model(pkts)

    async def send():
        for p in pkts:
            await drive_axis(dut, p)
    tx = cocotb.start_soon(send())
    rx = cocotb.start_soon(recv_axis(dut, len(exp), throttle=throttle, rng=rng))
    await Combine(tx, rx)
    got = rx.result()
    for i, ((g, t0, tl), (e, total, drop)) in enumerate(zip(got, exp)):
        assert g == e, (f"packet {i} mismatch (len {len(e)} vs {len(g)}): "
                        f"{g[:20].hex()} != {e[:20].hex()}")
        assert t0 == total, f"packet {i} tuser@beat0={t0} != total {total}"
        assert (tl & 1) == int(drop), f"packet {i} drop={tl & 1} != {int(drop)}"
    await quiet_check(dut)
    return got


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

@cocotb.test()
async def test_single_packet(dut):
    """One packet passes byte-exactly; tuser = total length @ beat0, no drop."""
    await reset(dut)
    await forward(dut, [Pkt(can_id=INF | 0x10, pkt_idx=0, pld_len=40, seed=1)])


@cocotb.test()
async def test_fragmented_request(dut):
    """idx increments across full-size fragments; a short fragment closes the
    request; the next request needs a higher ID."""
    await reset(dut)
    a, b = INF | 0x20, INF | 0x21
    pkts = [
        Pkt(a, 0, PLD_MAX, seed=1),   # full fragment: request stays open
        Pkt(a, 1, PLD_MAX, seed=2),   # full fragment: still open
        Pkt(a, 2, 300, seed=3),       # short fragment: closes the request
        Pkt(b, 0, 25, seed=4),        # new request
    ]
    assert len(model(pkts)) == 4
    await forward(dut, pkts)


@cocotb.test()
async def test_continuation_closed(dut):
    """A continuation after a short (closing) fragment is suppressed."""
    await reset(dut)
    a, b = INF | 0x30, INF | 0x31
    pkts = [
        Pkt(a, 0, 100, seed=1),       # short: request closed immediately
        Pkt(a, 1, 100, seed=2),       # fail: nothing open to continue
        Pkt(b, 0, 30, seed=3),        # pass: new request
    ]
    assert len(model(pkts)) == 2
    await forward(dut, pkts)


@cocotb.test()
async def test_non_inference(dut):
    """Non-inference packets are single-packet (PKT_IDX=0), ride their own
    increasing ID_cont, and advance the ordering like any other packet."""
    await reset(dut)
    pkts = [
        Pkt(0x100, 0, 50, seed=1),        # pass: non-inference, new ID_cont
        Pkt(0x101, 0, 60, seed=2),        # pass: higher ID_cont
        Pkt(INF | 0x102, 0, PLD_MAX, seed=3),  # pass: inference, opens request
        Pkt(INF | 0x102, 1, 200, seed=4),      # pass: inference continuation
        Pkt(0x101, 0, 50, seed=5),        # fail: ID_cont went backwards
        Pkt(0x103, 4, 50, seed=6),        # fail: non-inference PKT_IDX != 0
        Pkt(0, 0, 50, seed=7),            # fail: ID=0 reserved (never advances)
    ]
    assert len(model(pkts)) == 4
    await forward(dut, pkts)


@cocotb.test()
async def test_varying_payload(dut):
    """Payload sizes exercising every last-beat tkeep, up to the max."""
    await reset(dut)
    pkts = [Pkt(can_id=INF | (0x100 + i), pkt_idx=0, pld_len=plen, seed=plen)
            for i, plen in enumerate([1, 2, 3, 4, 5, 16, 33, 100, 255, PLD_MAX])]
    await forward(dut, pkts)


@cocotb.test()
async def test_header_fail_suppressed(dut):
    """Header-check failures emit nothing; the sequence state is unaffected,
    so a correct follow-up packet still passes."""
    await reset(dut)
    a, b = INF | 0x50, INF | 0x51
    pkts = [
        Pkt(a, 0, PLD_MAX, seed=1),                      # pass: opens request
        Pkt(INF | 0x4F, 0, 20, seed=2),                  # fail: ID went backwards
        Pkt(a, 2, 20, seed=3),                           # fail: idx skips (0 -> 2)
        Pkt(b, 1, 20, seed=4),                           # fail: new ID, idx != 0
        Pkt(b, 0, 0, payload_len=20, seed=5),            # fail: pld_len = 0
        Pkt(b, 0, PLD_MAX + 1, payload_len=20, seed=6),  # fail: pld_len range
        Pkt(a, 1, 20, seed=7),                           # pass: continues, closes
        Pkt(a, 2, 20, seed=8),                           # fail: request closed
        Pkt(b, 0, 20, seed=9),                           # pass: new request
    ]
    assert len(model(pkts)) == 3
    await forward(dut, pkts)


@cocotb.test()
async def test_first_packet_idx(dut):
    """The first packet after reset must start a request (idx 0)."""
    await reset(dut)
    pkts = [
        Pkt(INF | 0x60, pkt_idx=3, pld_len=20, seed=1),   # fail: mid-request
        Pkt(INF | 0x60, pkt_idx=0, pld_len=20, seed=2),   # pass
    ]
    assert len(model(pkts)) == 1
    await forward(dut, pkts)


@cocotb.test()
async def test_pld_len_mismatch(dut):
    """A header lying about pld_len forwards with the drop flag; the dropped
    packet doesn't advance the sequence, so a retransmit (same idx) passes."""
    await reset(dut)
    a = INF | 0x70
    pkts = [
        Pkt(a, 0, PLD_MAX, seed=1),                      # pass: opens request
        Pkt(a, 1, 99, payload_len=40, seed=2),           # drop: short payload
        Pkt(a, 1, 30, payload_len=44, seed=3),           # drop: long payload
        Pkt(a, 1, 40, seed=4),                           # pass: retransmit
    ]
    exp = model(pkts)
    assert [d for (_, _, d) in exp] == [False, True, True, False]
    await forward(dut, pkts)


@cocotb.test()
async def test_upstream_drop_flag(dut):
    """The upstream truncation flag propagates as the drop flag at tlast even
    when the canonical byte count matches the header."""
    await reset(dut)
    pkts = [
        Pkt(INF | 0x80, 0, 40, in_drop=True, seed=1),    # drop: flag only
        Pkt(INF | 0x80, 0, 40, seed=2),                  # pass: retransmit
    ]
    exp = model(pkts)
    assert [d for (_, _, d) in exp] == [True, False]
    await forward(dut, pkts)


@cocotb.test()
async def test_runt(dut):
    """Packets too short to carry a full header are suppressed."""
    await reset(dut)
    pkts = ([Pkt(0, 0, 0, raw_len=n, seed=n) for n in [1, 4, 8, 11, 12]]
            + [Pkt(INF | 0x90, 0, 20, seed=1)])  # pass
    assert len(model(pkts)) == 1
    await forward(dut, pkts)


@cocotb.test()
async def test_backpressure(dut):
    """Sink randomly throttles tready; nothing lost or reordered."""
    await reset(dut)
    pkts = [Pkt(can_id=INF | (0xA0 + i), pkt_idx=0, pld_len=20 + 7 * i, seed=200 + i)
            for i in range(6)]
    await forward(dut, pkts, throttle=0.4, rng=random.Random(9))


@cocotb.test()
async def test_mixed_stream(dut):
    """Long randomized mix of fragmented requests, non-inference packets,
    header failures, drops and runts under backpressure, vs the model."""
    await reset(dut)
    rng = random.Random(42)
    pkts = []
    can_id, idx, open_req = INF | 0x1000, 0, False
    for i in range(40):
        kind = rng.random()
        if kind < 0.45:  # well-formed traffic
            if open_req and rng.random() < 0.5:
                full = rng.random() < 0.4
                pkts.append(Pkt(can_id, idx, PLD_MAX if full else rng.randint(1, 80), seed=i))
                idx += 1
                open_req = full
            else:
                can_id += rng.randint(1, 5)
                idx = 0
                full = rng.random() < 0.3
                pkts.append(Pkt(can_id, idx, PLD_MAX if full else rng.randint(1, 80), seed=i))
                idx += 1
                open_req = full
        elif kind < 0.55:  # ID=0 (reserved): always suppressed
            pkts.append(Pkt(0, 0, rng.randint(1, 80), seed=i))
        elif kind < 0.7:  # header failure: stale ID or bad idx
            pkts.append(Pkt(can_id - 1, rng.randint(0, 2), rng.randint(1, 80), seed=i))
        elif kind < 0.8:  # payload length mismatch -> emitted, dropped
            plen = rng.randint(1, 80)
            pkts.append(Pkt(can_id, idx if open_req else 0,
                            plen, payload_len=plen + rng.randint(1, 9), seed=i))
        elif kind < 0.9:  # upstream truncation -> emitted, dropped
            pkts.append(Pkt(can_id + rng.randint(1, 3), 0,
                            rng.randint(1, 80), in_drop=True, seed=i))
        else:  # runt
            pkts.append(Pkt(0, 0, 0, raw_len=rng.randint(1, 12), seed=i))
    await forward(dut, pkts, throttle=0.3, rng=random.Random(7))
