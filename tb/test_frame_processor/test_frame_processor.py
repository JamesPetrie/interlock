"""cocotb testbench for frame_processor.sv.

frame_processor parses the Ethernet + canonical headers out of a MAC-FIFO
frame, stores the payload in a ping-pong RAM, then re-streams the frame
(reconstructed from the header structs + payload RAM) while the other slot
receives the next frame.

Because no sanitization happens yet, the header structs round-trip
byte-exactly, so output must equal input bit-for-bit. We additionally check
the parsed header fields exposed on the dbg_* ports.

MAC-FIFO protocol (see fabric_bridge.sv / CoreTSE_tb.v):
  * 32-bit word, bytes little-endian (byte 0 in bits [7:0]).
  * RDY = source-valid, ACPT = sink-ready; transfer when both high.
  * SOF on the first word, EOF on the last.
  * BYTEVALID[1:0] = count of *unused* bytes in the final word.
"""
import random
from dataclasses import dataclass

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, Combine


# --------------------------------------------------------------------------
# Frame construction — header fields are big-endian on the wire (first octet
# is the MSB), matching eth_pkg / canonical_pkg's *_from_le helpers.
# --------------------------------------------------------------------------

ETH_HDR_BYTES = 14
CANON_HDR_BYTES = 12
HDR_BYTES = ETH_HDR_BYTES + CANON_HDR_BYTES   # 26


@dataclass
class Hdr:
    dst: int
    src: int
    eth_len: int
    can_id: int
    pkt_idx: int
    pld_len: int


def build_frame(hdr: Hdr, total_bytes: int, seed: int = 0) -> bytes:
    """Eth header (14) + canonical header (12) + random payload, `total_bytes`
    long. The trailing 4 bytes stand in for the FCS but are treated as opaque
    payload by frame_processor."""
    assert total_bytes >= 64, "use >= 64-byte (>= 16-word) frames"
    head = (
        hdr.dst.to_bytes(6, "big")
        + hdr.src.to_bytes(6, "big")
        + hdr.eth_len.to_bytes(2, "big")
        + hdr.can_id.to_bytes(8, "big")
        + hdr.pkt_idx.to_bytes(2, "big")
        + hdr.pld_len.to_bytes(2, "big")
    )
    assert len(head) == HDR_BYTES
    rng = random.Random(seed)
    payload = bytes(rng.randint(0, 255) for _ in range(total_bytes - HDR_BYTES))
    return head + payload


def sample_hdr(seed: int) -> Hdr:
    rng = random.Random(seed)
    return Hdr(
        dst=rng.getrandbits(48),
        src=rng.getrandbits(48),
        eth_len=rng.getrandbits(16),
        can_id=rng.getrandbits(64),
        pkt_idx=rng.getrandbits(16),
        pld_len=rng.getrandbits(16),
    )


# --------------------------------------------------------------------------
# MAC-FIFO word packing
# --------------------------------------------------------------------------

@dataclass
class Word:
    dat: int
    sof: int
    eof: int
    bytevalid: int


def pack_frame(payload: bytes):
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
# Driver / monitor
# --------------------------------------------------------------------------

async def drive_frame(dut, payload):
    words = pack_frame(payload)
    i = 0
    while i < len(words):
        w = words[i]
        dut.in_rdy.value = 1
        dut.in_dat.value = w.dat
        dut.in_sof.value = w.sof
        dut.in_eof.value = w.eof
        dut.in_bytevalid.value = w.bytevalid
        await ReadOnly()
        accepted = int(dut.in_acpt.value)
        await RisingEdge(dut.clk)
        if accepted:
            i += 1
    dut.in_rdy.value = 0
    dut.in_sof.value = 0
    dut.in_eof.value = 0
    dut.in_bytevalid.value = 0


async def receive_frames(dut, expected_frames, throttle_prob=0.0, rng=None):
    if rng is None:
        rng = random.Random(0)
    received = []
    cur = bytearray()
    at_frame_start = True
    while len(received) < expected_frames:
        dut.out_acpt.value = 0 if rng.random() < throttle_prob else 1
        await ReadOnly()
        rdy = int(dut.out_rdy.value)
        acpt = int(dut.out_acpt.value)
        dat = int(dut.out_dat.value)
        sof = int(dut.out_sof.value)
        eof = int(dut.out_eof.value)
        bv = int(dut.out_bytevalid.value)
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
    dut.out_acpt.value = 0
    return received


async def reset(dut, period_ns=8):
    cocotb.start_soon(Clock(dut.clk, period_ns, unit="ns").start())
    dut.rst_n.value = 0
    dut.in_rdy.value = 0
    dut.in_sof.value = 0
    dut.in_eof.value = 0
    dut.in_dat.value = 0
    dut.in_bytevalid.value = 0
    dut.out_acpt.value = 0
    for _ in range(5):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    for _ in range(2):
        await RisingEdge(dut.clk)


async def forward(dut, payloads, throttle_prob=0.0, rng=None):
    """Drive all payloads in, collect all out, assert byte-exact + in order."""
    async def send_all():
        for p in payloads:
            await drive_frame(dut, p)

    tx = cocotb.start_soon(send_all())
    rx = cocotb.start_soon(receive_frames(dut, len(payloads),
                                          throttle_prob=throttle_prob, rng=rng))
    await Combine(tx, rx)
    got = rx.result()
    assert len(got) == len(payloads), f"expected {len(payloads)} frames, got {len(got)}"
    for idx, (g, exp) in enumerate(zip(got, payloads)):
        assert g == exp, (
            f"frame {idx} mismatch (len {len(exp)} vs {len(g)}): "
            f"{g[:32].hex()} != {exp[:32].hex()}"
        )
    return got


def check_dbg(dut, hdr: Hdr):
    # The module now exposes only canonical-header debug fields; Ethernet-header
    # correctness is covered by the byte-exact forward checks.
    assert int(dut.dbg_hdr_valid.value) == 1, "dbg_hdr_valid not set"
    assert int(dut.dbg_can_id.value) == hdr.can_id, "canonical id mismatch"
    assert int(dut.dbg_can_pkt_idx.value) == hdr.pkt_idx, "canonical pkt_idx mismatch"
    assert int(dut.dbg_can_pld_len.value) == hdr.pld_len, "canonical pld_len mismatch"


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

@cocotb.test()
async def test_single_frame(dut):
    """One frame round-trips byte-exactly and its headers are extracted."""
    await reset(dut)
    hdr = sample_hdr(1)
    frame = build_frame(hdr, 64, seed=1)
    await forward(dut, [frame])
    check_dbg(dut, hdr)


@cocotb.test()
async def test_varying_sizes(dut):
    """Sizes hitting every BYTEVALID remainder (len % 4 = 0/1/2/3)."""
    await reset(dut)
    sizes = [64, 65, 66, 67, 73, 128, 200, 256, 511, 1518]
    for s in sizes:
        hdr = sample_hdr(s)
        frame = build_frame(hdr, s, seed=s)
        await forward(dut, [frame])
        check_dbg(dut, hdr)


@cocotb.test()
async def test_header_extraction_known(dut):
    """Hand-picked header values land in the right struct fields."""
    await reset(dut)
    hdr = Hdr(
        dst=0x02_00_00_00_00_AA,
        src=0x02_00_00_00_00_BB,
        eth_len=0x0026,
        can_id=0x8001_0203_0405_0607,   # inf bit (MSB) set
        pkt_idx=0x1234,
        pld_len=0x00C0,
    )
    frame = build_frame(hdr, 96, seed=7)
    await forward(dut, [frame])
    check_dbg(dut, hdr)


@cocotb.test()
async def test_pingpong_stream(dut):
    """Several back-to-back frames exercise the ping-pong slots; all must
    come out in order, byte-exact."""
    await reset(dut)
    frames = [build_frame(sample_hdr(100 + i), 64 + 4 * i, seed=100 + i)
              for i in range(8)]
    await forward(dut, frames)


@cocotb.test()
async def test_backpressure(dut):
    """Output sink randomly throttles ACPT; no bytes may be lost or reordered."""
    await reset(dut)
    frames = [build_frame(sample_hdr(200 + i), 80 + 13 * i, seed=200 + i)
              for i in range(6)]
    await forward(dut, frames, throttle_prob=0.4, rng=random.Random(9))
