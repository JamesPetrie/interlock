"""cocotb testbench for fabric_bridge.sv.

fabric_bridge routes both Ethernet directions through the fabric, sanitizing
each at the Ethernet layer (eth_deframe -> eth_reframe, canon_core bypassed
for now): whatever DST/SRC/PAD/FCS a frame arrives with, it leaves with the
direction's forced addresses, LENGTH preserved, zero PAD to the minimum frame
size, and a freshly computed FCS. This TB models the two CoreTSE MACs at the
module boundary and checks that the DATA forwards intact inside a canonical
frame in both directions.

MAC client (packet-FIFO) protocol — taken from Microchip's reference
testbench CoreTSE_tb.v (tasks ftfrm / frfrm):

  * 32-bit data word, bytes packed little-endian (byte 0 in bits [7:0]).
  * RDY = source-valid, ACPT = sink-ready; a word transfers on a rising
    edge where both are high (valid/ready handshake).
  * SOF on the first word of a frame, EOF on the last.
  * BYTEVALID[1:0] is the count of *unused* bytes in the final word
    (0 on full words; 3/2/1 when the frame ends with 1/2/3 valid bytes).
    valid_bytes = 4 - BYTEVALID.

The module is single-clock (both CoreTSE MAC client interfaces run on the
fabric clock), so one clk drives source and sink.
"""
import random
import zlib
from dataclasses import dataclass
from types import SimpleNamespace

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, Combine

# Must match fabric_bridge localparams
MAC_CLIENT = 0x02_00_00_00_00_01
MAC_SERVER = 0x02_00_00_00_00_02

MIN_FRAME = 64
FCS_BYTES = 4


def eth_fcs(body: bytes) -> bytes:
    """Ethernet/zlib CRC32 of `body` (Dst+Src+Length+Data+Pad — the
    FCS-protected fields of a MAC frame), packed little-endian (wire order)."""
    return zlib.crc32(body).to_bytes(4, "little")


# --------------------------------------------------------------------------
# Frame builders
# --------------------------------------------------------------------------

def eth_frame(dst: int, src: int, data: bytes, pad: bytes = None,
              fcs: bytes = None) -> bytes:
    """Assemble DST SRC LENGTH DATA PAD FCS. LENGTH = len(data). PAD defaults
    to zeros up to the 64-byte minimum frame; pass `pad`/`fcs` to build a
    non-canonical frame."""
    body = (dst.to_bytes(6, "big") + src.to_bytes(6, "big")
            + len(data).to_bytes(2, "big") + data)
    if pad is None:
        if len(body) + FCS_BYTES < MIN_FRAME:
            body += bytes(MIN_FRAME - FCS_BYTES - len(body))
    else:
        body += pad
    return body + (eth_fcs(body) if fcs is None else fcs)


def payload(seed: int, n: int) -> bytes:
    rng = random.Random(seed)
    return bytes(rng.randint(0, 255) for _ in range(n))


def raw_frame(dst: int, src: int, len_field: int, data: bytes) -> bytes:
    """A raw wire frame with an ARBITRARY L/T field and no auto pad/FCS — the TB
    drives exactly these bytes (as the MAC would present them). Lets us craft
    malformed frames where the declared length disagrees with the actual data, to
    exercise reject / zero-fill / truncate-long."""
    return (dst.to_bytes(6, "big") + src.to_bytes(6, "big")
            + len_field.to_bytes(2, "big") + data)


def eth_ii_frame(dst: int, src: int, ethertype: int, data: bytes) -> bytes:
    """Ethernet II / TYPE frame: DST SRC ETHERTYPE DATA [PAD] FCS. The L/T field
    holds an EtherType (> 1500), which an 802.3-LENGTH deframer mis-reads as a
    LENGTH. 0x86DD=IPv6, 0x0800=IPv4, 0x0806=ARP — the bulk of real traffic."""
    body = (dst.to_bytes(6, "big") + src.to_bytes(6, "big")
            + ethertype.to_bytes(2, "big") + data)
    if len(body) + FCS_BYTES < MIN_FRAME:
        body += bytes(MIN_FRAME - FCS_BYTES - len(body))
    return body + eth_fcs(body)


# --------------------------------------------------------------------------
# Frame <-> MAC-FIFO word packing
# --------------------------------------------------------------------------

@dataclass
class Word:
    dat: int
    sof: int
    eof: int
    bytevalid: int   # count of UNUSED bytes in this word (0 unless final partial)


def pack_frame(payload: bytes):
    """Split a byte string into MAC-FIFO words with SOF/EOF/BYTEVALID set the
    way a CoreTSE MAC RX would present them."""
    L = len(payload)
    nwords = (L + 3) // 4
    words = []
    for w in range(nwords):
        chunk = payload[4 * w: 4 * w + 4]
        dat = 0
        for k, byte in enumerate(chunk):
            dat |= byte << (8 * k)
        is_last = (w == nwords - 1)
        rem = L % 4
        bytevalid = (4 - rem) if (is_last and rem != 0) else 0
        words.append(Word(dat=dat, sof=int(w == 0), eof=int(is_last), bytevalid=bytevalid))
    return words


# --------------------------------------------------------------------------
# Signal bundles — one MAC-FIFO interface, addressed by pin prefix
# --------------------------------------------------------------------------

def bundle(dut, prefix):
    g = lambda s: getattr(dut, prefix + s)
    return SimpleNamespace(rdy=g("rdy"), acpt=g("acpt"), sof=g("sof"),
                           eof=g("eof"), dat=g("dat"), bytevalid=g("bytevalid"))


# --------------------------------------------------------------------------
# Driver (MAC RX source) / monitor (MAC TX sink)
# --------------------------------------------------------------------------

async def drive_frame(dut, src, payload):
    """Present one frame on a MAC-RX bundle, honoring ACPT backpressure."""
    words = pack_frame(payload)
    i = 0
    while i < len(words):
        w = words[i]
        src.rdy.value = 1
        src.dat.value = w.dat
        src.sof.value = w.sof
        src.eof.value = w.eof
        src.bytevalid.value = w.bytevalid
        await ReadOnly()
        accepted = int(src.acpt.value)
        await RisingEdge(dut.clk)
        if accepted:
            i += 1
    src.rdy.value = 0
    src.sof.value = 0
    src.eof.value = 0
    src.bytevalid.value = 0


async def receive_frames(dut, sink, expected_frames, throttle_prob=0.0, rng=None):
    """Collect complete frames off a MAC-TX bundle. Returns list[bytes]."""
    if rng is None:
        rng = random.Random(0)
    received = []
    cur = bytearray()
    at_frame_start = True
    while len(received) < expected_frames:
        sink.acpt.value = 0 if rng.random() < throttle_prob else 1
        await ReadOnly()
        rdy = int(sink.rdy.value)
        acpt = int(sink.acpt.value)
        dat = int(sink.dat.value)
        sof = int(sink.sof.value)
        eof = int(sink.eof.value)
        bv = int(sink.bytevalid.value)
        await RisingEdge(dut.clk)
        if rdy and acpt:
            assert sof == at_frame_start, \
                f"SOF framing error: expected sof={int(at_frame_start)}, got {sof}"
            valid = 4 - bv
            for k in range(valid):
                cur.append((dat >> (8 * k)) & 0xFF)
            at_frame_start = False
            if eof:
                received.append(bytes(cur))
                cur = bytearray()
                at_frame_start = True
    sink.acpt.value = 0
    return received


# --------------------------------------------------------------------------
# Scaffolding
# --------------------------------------------------------------------------

# (source-bundle prefix, sink-bundle prefix, forced DST, forced SRC).
REQ = ("tse0_mrx_", "tse1_mtx_", MAC_SERVER, MAC_CLIENT)   # client -> server
RSP = ("tse1_mrx_", "tse0_mtx_", MAC_CLIENT, MAC_SERVER)   # server -> client


def sanitized(direction, data: bytes) -> bytes:
    """The frame the bridge must emit for `data` on `direction`."""
    return eth_frame(direction[2], direction[3], data)


async def reset(dut, period_ns=8):
    cocotb.start_soon(Clock(dut.clk, period_ns, unit="ns").start())
    dut.rst_n.value = 0
    for pfx in ("tse0_mrx_", "tse1_mrx_"):
        b = bundle(dut, pfx)
        b.rdy.value = 0; b.sof.value = 0; b.eof.value = 0
        b.dat.value = 0; b.bytevalid.value = 0
    for pfx in ("tse0_mtx_", "tse1_mtx_"):
        bundle(dut, pfx).acpt.value = 0
    for _ in range(5):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    for _ in range(2):
        await RisingEdge(dut.clk)


async def forward_check(dut, direction, frames, datas, throttle_prob=0.0, rng=None):
    """Drive `frames` into a direction; expect sanitized(data) out for each."""
    src = bundle(dut, direction[0])
    sink = bundle(dut, direction[1])

    async def send_all():
        for f in frames:
            await drive_frame(dut, src, f)

    tx = cocotb.start_soon(send_all())
    rx = cocotb.start_soon(receive_frames(dut, sink, len(frames),
                                          throttle_prob=throttle_prob, rng=rng))
    await Combine(tx, rx)
    got = rx.result()
    assert len(got) == len(frames), f"expected {len(frames)} frames, got {len(got)}"
    for idx, (g, data) in enumerate(zip(got, datas)):
        exp = sanitized(direction, data)
        assert g == exp, (f"frame {idx} mismatch (len {len(g)} vs {len(exp)}): "
                          f"{g[:20].hex()} != {exp[:20].hex()}")


def canonical_frames(direction, datas):
    """Input frames already in the direction's canonical form — the output
    must then reproduce the input byte-for-byte."""
    return [eth_frame(direction[2], direction[3], d) for d in datas]


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

@cocotb.test()
async def test_forward_req(dut):
    """One padded minimum frame client -> server (CORETSE_0 RX -> CORETSE_1 TX)."""
    await reset(dut)
    datas = [payload(1, 18)]
    await forward_check(dut, REQ, canonical_frames(REQ, datas), datas)


@cocotb.test()
async def test_forward_rsp(dut):
    """One frame server -> client; forced addresses are the request's swapped."""
    await reset(dut)
    datas = [payload(2, 46)]
    await forward_check(dut, RSP, canonical_frames(RSP, datas), datas)


@cocotb.test()
async def test_varying_sizes(dut):
    """Every LENGTH % 4 alignment, padded and unpadded sizes, up to max DATA."""
    await reset(dut)
    sizes = [13, 14, 15, 16, 45, 46, 47, 48, 49, 100, 1497, 1498, 1499, 1500]
    datas = [payload(s, s) for s in sizes]
    await forward_check(dut, REQ, canonical_frames(REQ, datas), datas)


@cocotb.test()
async def test_sanitization(dut):
    """Junk DST/SRC, garbage PAD and a bogus FCS go in; a fully canonical
    frame comes out (forced addresses, zero PAD, recomputed FCS)."""
    await reset(dut)
    rng = random.Random(99)
    frames, datas = [], []
    for n in (18, 46, 60):
        data = payload(500 + n, n)
        pad = bytes(rng.randint(1, 255) for _ in range(max(0, 46 - n)))
        frames.append(eth_frame(rng.getrandbits(48), rng.getrandbits(48), data,
                                pad=pad, fcs=b"\xde\xad\xbe\xef"))
        datas.append(data)
    await forward_check(dut, REQ, frames, datas)


@cocotb.test()
async def test_backpressure(dut):
    """Server-side MAC TX randomly throttles ACPT; no bytes may be lost."""
    await reset(dut)
    datas = [payload(100 + i, 50 + 13 * i) for i in range(6)]
    await forward_check(dut, REQ, canonical_frames(REQ, datas), datas,
                        throttle_prob=0.4, rng=random.Random(7))


@cocotb.test()
async def test_type_frame_rejected_and_recovers(dut):
    """A TYPE frame (EtherType 0x86DD > 1500) is now SILENTLY DROPPED (no egress,
    no wedge), and a following good LENGTH frame forwards intact. This is the
    fix for the prior on-silicon wedge: a non-conforming / crafted frame can
    neither exfiltrate nor brick the pipe."""
    await reset(dut)
    src = bundle(dut, REQ[0])
    sink = bundle(dut, REQ[1])
    good = payload(7, 40)

    async def send():
        await drive_frame(dut, src, eth_ii_frame(0x11, 0x22, 0x86DD, payload(1, 40)))
        await drive_frame(dut, src, eth_frame(REQ[2], REQ[3], good))

    tx = cocotb.start_soon(send())
    rx = cocotb.start_soon(receive_frames(dut, sink, 1))   # only the good frame egresses
    await Combine(tx, rx)
    got = rx.result()
    assert len(got) == 1, f"expected 1 forwarded frame (TYPE dropped), got {len(got)}"
    assert got[0] == sanitized(REQ, good), "good frame after a dropped TYPE frame must forward intact"
    assert int(dut.dbg_df_type_count.value) == 1, "TYPE frame should be counted as rejected"
    assert int(dut.dbg_df_sof_count.value) == 2, "both frames entered deframe"
    assert int(dut.dbg_df_emit_frames.value) == 1, "only the good frame should be emitted"
    assert int(dut.dbg_rf_state.value) == 0, "reframe must be idle (never wedged on the TYPE frame)"


@cocotb.test()
async def test_zero_fill_short_frame(dut):
    """L/T (100) > actual data (40): zero-fill to LENGTH. Egress = [40 real][60 zero]."""
    await reset(dut)
    src = bundle(dut, REQ[0])
    sink = bundle(dut, REQ[1])
    real = payload(3, 40)
    tx = cocotb.start_soon(drive_frame(dut, src, raw_frame(0xAA, 0xBB, 100, real)))
    rx = cocotb.start_soon(receive_frames(dut, sink, 1))
    await Combine(tx, rx)
    got = rx.result()
    assert got[0] == sanitized(REQ, real + bytes(60)), \
        f"zero-fill mismatch: len {len(got[0])} vs {len(sanitized(REQ, real + bytes(60)))}"
    assert int(dut.dbg_df_trunc_count.value) == 1, "short frame should be counted (zero-filled)"
    assert int(dut.dbg_rf_last_fwd_len.value) == 100
    assert int(dut.dbg_rf_state.value) == 0


@cocotb.test()
async def test_truncate_long_frame(dut):
    """actual data (100) > L/T (40): forward exactly 40, silently discard the rest."""
    await reset(dut)
    src = bundle(dut, REQ[0])
    sink = bundle(dut, REQ[1])
    data = payload(4, 100)
    tx = cocotb.start_soon(drive_frame(dut, src, raw_frame(0xCC, 0xDD, 40, data)))
    rx = cocotb.start_soon(receive_frames(dut, sink, 1))
    await Combine(tx, rx)
    assert rx.result()[0] == sanitized(REQ, data[:40]), "must forward exactly the first 40 octets"
    assert int(dut.dbg_rf_last_fwd_len.value) == 40
    assert int(dut.dbg_rf_state.value) == 0


@cocotb.test()
async def test_length_boundary(dut):
    """LEN=1500 accepted (max); LEN=1501 rejected (silent drop)."""
    await reset(dut)
    src = bundle(dut, REQ[0])
    sink = bundle(dut, REQ[1])
    d1500 = payload(5, 1500)
    tx = cocotb.start_soon(drive_frame(dut, src, eth_frame(REQ[2], REQ[3], d1500)))
    rx = cocotb.start_soon(receive_frames(dut, sink, 1))
    await Combine(tx, rx)
    assert rx.result()[0] == sanitized(REQ, d1500), "LEN=1500 must forward"
    good = payload(6, 50)

    async def send():
        await drive_frame(dut, src, raw_frame(0x1, 0x2, 1501, payload(9, 200)))
        await drive_frame(dut, src, eth_frame(REQ[2], REQ[3], good))

    tx2 = cocotb.start_soon(send())
    rx2 = cocotb.start_soon(receive_frames(dut, sink, 1))
    await Combine(tx2, rx2)
    assert rx2.result()[0] == sanitized(REQ, good), "LEN=1501 dropped; next frame forwards"


@cocotb.test()
async def test_type_frame_rejected_rsp_direction(dut):
    """Reject + recovery on the response path (port1 -> port0)."""
    await reset(dut)
    src = bundle(dut, RSP[0])
    sink = bundle(dut, RSP[1])
    good = payload(8, 46)

    async def send():
        await drive_frame(dut, src, eth_ii_frame(0x11, 0x22, 0x0800, payload(2, 30)))
        await drive_frame(dut, src, eth_frame(RSP[2], RSP[3], good))

    tx = cocotb.start_soon(send())
    rx = cocotb.start_soon(receive_frames(dut, sink, 1))
    await Combine(tx, rx)
    assert rx.result()[0] == sanitized(RSP, good), "rsp good frame must forward after a dropped TYPE frame"


async def _idle(dut, n):
    for _ in range(n):
        await RisingEdge(dut.clk)


@cocotb.test()
async def test_instrumentation_length_frame(dut):
    """A normal 802.3 LENGTH frame: the instrumentation taps must report a clean
    frame — sof_count=1, first_lt=L, type_count=0, trunc_count=0, and reframe
    sampled the SAME length in-band at SOF (tuser_at_sof=L)."""
    await reset(dut)
    src = bundle(dut, REQ[0])
    sink = bundle(dut, REQ[1])
    sink.acpt.value = 1
    L = 40
    frame = eth_frame(REQ[2], REQ[3], payload(11, L))
    await drive_frame(dut, src, frame)
    await _idle(dut, 60)
    assert int(dut.dbg_df_sof_count.value) == 1
    assert int(dut.dbg_df_first_lt.value) == L
    assert int(dut.dbg_df_type_count.value) == 0
    assert int(dut.dbg_df_trunc_count.value) == 0
    assert int(dut.dbg_rf_tuser_at_sof.value) == L, \
        "reframe must sample the SAME length deframe reported (in-band, at SOF)"


@cocotb.test()
async def test_instrumentation_type_frame(dut):
    """An Ethernet II / TYPE frame is REJECTED: type_count=1 and first_lt=0x86DD
    (the on-silicon signature of real IPv6/IPv4/ARP traffic), but it never reaches
    reframe — so it is NOT emitted (dfEmit=0), NOT zero-filled (trunc_count=0), and
    reframe never sampled it (tuser_at_sof stays 0). Silent, deterministic drop."""
    await reset(dut)
    src = bundle(dut, REQ[0])
    sink = bundle(dut, REQ[1])
    sink.acpt.value = 1
    frame = eth_ii_frame(0x001122334455, 0x66778899AABB, 0x86DD, payload(12, 40))
    await drive_frame(dut, src, frame)
    await _idle(dut, 60)
    assert int(dut.dbg_df_sof_count.value) == 1
    assert int(dut.dbg_df_first_lt.value) == 0x86DD
    assert int(dut.dbg_df_type_count.value) == 1, "TYPE frame counted as rejected"
    assert int(dut.dbg_df_trunc_count.value) == 0, "rejected frame is not emitted/zero-filled"
    assert int(dut.dbg_df_emit_frames.value) == 0, "rejected frame must not be emitted"
    assert int(dut.dbg_rf_tuser_at_sof.value) == 0, "reframe never saw the rejected frame"


@cocotb.test()
async def test_interlock_tap_byte_order(dut):
    """Drive a frame whose payload is a wire.py packet through the real deframe;
    the inline interlock_tap must parse the SAME declared length (first 4 bytes,
    big-endian) the packet was built with — confirming deframe's AXIS byte order
    matches axis32_to_bytes (the synth-vs-sim byte-order trap the adapter warns
    about). Also confirms the frame still forwards with the tap inline."""
    import sys
    sys.path.insert(0, "/root/fpga/interlock/prototype")
    import wire as Wp
    await reset(dut)
    src = bundle(dut, REQ[0])
    sink = bundle(dut, REQ[1])
    pkt = Wp.output_packet(7, b"\xa5" * 24)            # a real wire.py packet
    assert len(pkt) <= 1500
    expect_len = int.from_bytes(pkt[0:4], "big")        # its declared-length header
    frame = eth_frame(REQ[2], REQ[3], pkt)
    tx = cocotb.start_soon(drive_frame(dut, src, frame))
    rx = cocotb.start_soon(receive_frames(dut, sink, 1))
    await Combine(tx, rx)
    assert rx.result()[0] == sanitized(REQ, pkt), "frame must still forward with the tap inline"
    got = int(dut.tap_req.dbg_pr_length.value)
    dut._log.info(f"tap pr_length=0x{got:08x} expect=0x{expect_len:08x}")
    assert got == expect_len, \
        f"deframe->adapter byte-order mismatch: tap parsed 0x{got:08x}, expected 0x{expect_len:08x}"


@cocotb.test()
async def test_bidirectional_concurrent(dut):
    """Both directions active at once — verify no cross-talk between the two
    independent sanitizing paths."""
    await reset(dut)
    req = [payload(300 + i, 40 + 4 * i) for i in range(4)]
    rsp = [payload(400 + i, 70 + 7 * i) for i in range(4)]
    a = cocotb.start_soon(forward_check(dut, REQ, canonical_frames(REQ, req), req,
                                        throttle_prob=0.2, rng=random.Random(1)))
    b = cocotb.start_soon(forward_check(dut, RSP, canonical_frames(RSP, rsp), rsp,
                                        throttle_prob=0.2, rng=random.Random(2)))
    await Combine(a, b)
