"""cocotb testbench for fabric_bridge.sv.

fabric_bridge is the module that routes both Ethernet directions through the
fabric (replacing the direct CoreTSE-to-CoreTSE wiring), giving packet
processing somewhere to live. This TB models the two CoreTSE MACs at the
module boundary and checks that frames forward byte-exactly in both
directions.

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
from scapy.all import Ether


def eth_fcs(body: bytes) -> bytes:
    """Ethernet/zlib CRC32 of `body` (Dst+Src+Type+Data+Pad — the FCS-protected
    fields of a MAC frame), packed little-endian (wire order)."""
    return zlib.crc32(body).to_bytes(4, "little")


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


def build_ether(size_bytes, payload_seed=0):
    """Build an ethernet-shaped frame of total length `size_bytes`, where the
    last 4 bytes are a valid CRC32 over the preceding `size_bytes-4` bytes —
    i.e. the same shape CoreTSE delivers on its MAC client interface (frame
    body + FCS, where "body" = Dst+Src+Type+Data+Pad). When this frame passes
    through fcs_recalc, the output FCS is recomputed but must equal the
    input FCS (since the body is unchanged), so output == input.

    Total length must be >= 5 bytes (fcs_recalc requires >= 2 words)."""
    assert size_bytes >= 5, "frame must be >= 5 bytes"
    rng = random.Random(payload_seed)
    body_len = size_bytes - 4
    raw = bytes(Ether(src="02:00:00:00:00:01", dst="02:00:00:00:00:02", type=0x88B5))
    fill = max(0, body_len - len(raw))
    body = (raw + bytes(rng.randint(0, 255) for _ in range(fill)))[:body_len]
    return body + eth_fcs(body)


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

# (source-bundle prefix, sink-bundle prefix) for each direction.
A_TO_B = ("tse0_mrx_", "tse1_mtx_")   # Mac -> Spark
B_TO_A = ("tse1_mrx_", "tse0_mtx_")   # Spark -> Mac


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


async def forward_check(dut, direction, payloads, throttle_prob=0.0, rng=None):
    src = bundle(dut, direction[0])
    sink = bundle(dut, direction[1])

    async def send_all():
        for p in payloads:
            await drive_frame(dut, src, p)

    tx = cocotb.start_soon(send_all())
    rx = cocotb.start_soon(receive_frames(dut, sink, len(payloads),
                                          throttle_prob=throttle_prob, rng=rng))
    await Combine(tx, rx)
    got = rx.result()
    assert len(got) == len(payloads), f"expected {len(payloads)} frames, got {len(got)}"
    for idx, (g, exp) in enumerate(zip(got, payloads)):
        assert g == exp, f"frame {idx} mismatch (len {len(exp)}): {g[:16].hex()} != {exp[:16].hex()}"


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

@cocotb.test()
async def test_forward_a_to_b(dut):
    """One frame Mac -> Spark (CORETSE_0 RX -> CORETSE_1 TX)."""
    await reset(dut)
    await forward_check(dut, A_TO_B, [build_ether(64, payload_seed=1)])


@cocotb.test()
async def test_forward_b_to_a(dut):
    """One frame Spark -> Mac (CORETSE_1 RX -> CORETSE_0 TX)."""
    await reset(dut)
    await forward_check(dut, B_TO_A, [build_ether(64, payload_seed=2)])


@cocotb.test()
async def test_varying_sizes(dut):
    """Sizes that exercise every BYTEVALID remainder (len % 4 = 0/1/2/3)."""
    await reset(dut)
    sizes = [60, 61, 62, 63, 64, 73, 128, 200, 256, 1518]
    payloads = [build_ether(s, payload_seed=s) for s in sizes]
    await forward_check(dut, A_TO_B, payloads)


@cocotb.test()
async def test_backpressure(dut):
    """Spark-side MAC TX randomly throttles ACPT; no bytes may be lost."""
    await reset(dut)
    payloads = [build_ether(80 + 13 * i, payload_seed=100 + i) for i in range(6)]
    await forward_check(dut, A_TO_B, payloads, throttle_prob=0.4, rng=random.Random(7))


@cocotb.test()
async def test_bidirectional_concurrent(dut):
    """Both directions active at once — verify no cross-talk between the two
    independent forwarding paths."""
    await reset(dut)
    ab = [build_ether(64 + 4 * i, payload_seed=300 + i) for i in range(4)]
    ba = [build_ether(96 + 7 * i, payload_seed=400 + i) for i in range(4)]
    a2b = cocotb.start_soon(forward_check(dut, A_TO_B, ab,
                                          throttle_prob=0.2, rng=random.Random(1)))
    b2a = cocotb.start_soon(forward_check(dut, B_TO_A, ba,
                                          throttle_prob=0.2, rng=random.Random(2)))
    await Combine(a2b, b2a)


@cocotb.test()
async def test_fcs_is_recomputed(dut):
    """Drive a frame with a deliberately bogus FCS and verify fcs_recalc
    overwrites it with the correct CRC32. Covers each (length mod 4) so the
    byte-straddling logic between the penultimate and final words gets
    exercised at every alignment."""
    await reset(dut)
    src = bundle(dut, A_TO_B[0])
    sink = bundle(dut, A_TO_B[1])
    for size in (64, 65, 66, 67):
        rng = random.Random(900 + size)
        body = bytes(rng.randint(0, 255) for _ in range(size - 4))
        bad_frame = body + b"\x00\x00\x00\x00"   # last 4 bytes are NOT the CRC
        assert eth_fcs(body) != b"\x00\x00\x00\x00"
        tx = cocotb.start_soon(drive_frame(dut, src, bad_frame))
        rx = cocotb.start_soon(receive_frames(dut, sink, expected_frames=1))
        await Combine(tx, rx)
        got = rx.result()[0]
        assert len(got) == size, f"size {size}: length changed ({len(got)} != {size})"
        assert got[:-4] == body, f"size {size}: frame body was modified"
        assert got[-4:] == eth_fcs(body), (
            f"size {size}: FCS not recomputed; got {got[-4:].hex()} "
            f"expected {eth_fcs(body).hex()}"
        )
