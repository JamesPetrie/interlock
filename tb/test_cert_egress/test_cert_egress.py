"""Cert egress through fabric_bridge: with a fast bucket tick, the interlock cores
emit certificates which must be framed and muxed onto port-0 TX (toward the prover
frontend). Capture port-0 MAC-TX and check for cert frames (DST 02:..:CE) whose
payload begins "ilock-v5". No RX traffic needed — empty-bucket certs still emit."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge

CERT_DST = bytes.fromhex("02000000 00ce".replace(" ", ""))
import zlib


def eth_frame(dst, src, data):
    body = dst.to_bytes(6, "big") + src.to_bytes(6, "big") + len(data).to_bytes(2, "big") + data
    if len(body) + 4 < 64:
        body += bytes(64 - 4 - len(body))
    return body + zlib.crc32(body).to_bytes(4, "little")


async def drive_rx(dut, pfx, frame):
    """Drive one frame onto a MAC-RX bundle (honoring acpt)."""
    from cocotb.triggers import ReadOnly, RisingEdge
    L = len(frame)
    nw = (L + 3) // 4
    i = 0
    while i < nw:
        chunk = frame[4 * i:4 * i + 4]
        dat = 0
        for k, b in enumerate(chunk):
            dat |= b << (8 * k)
        rem = L % 4
        bv = (4 - rem) if (i == nw - 1 and rem) else 0
        getattr(dut, pfx + "rdy").value = 1
        getattr(dut, pfx + "dat").value = dat
        getattr(dut, pfx + "sof").value = 1 if i == 0 else 0
        getattr(dut, pfx + "eof").value = 1 if i == nw - 1 else 0
        getattr(dut, pfx + "bytevalid").value = bv
        await ReadOnly()
        acc = int(getattr(dut, pfx + "acpt").value)
        await RisingEdge(dut.clk)
        if acc:
            i += 1
    getattr(dut, pfx + "rdy").value = 0
    getattr(dut, pfx + "sof").value = 0
    getattr(dut, pfx + "eof").value = 0


async def forwarded_load(dut):
    """Continuous canonical response frames into port-1 RX -> reframe_rsp -> port-0
    TX mux in0. This concurrent load is what triggers the cert-egress deadlock."""
    n = 0
    while True:
        await drive_rx(dut, "tse1_mrx_", eth_frame(0xAABBCCDDEEFF, 0x112233445566,
                                                   bytes((n + i) & 0xFF for i in range(46))))
        n += 1


async def reset(dut):
    cocotb.start_soon(Clock(dut.clk, 8, unit="ns").start())
    for p in ("tse0_mrx_", "tse1_mrx_"):
        getattr(dut, p + "rdy").value = 0
        getattr(dut, p + "sof").value = 0
        getattr(dut, p + "eof").value = 0
        getattr(dut, p + "dat").value = 0
        getattr(dut, p + "bytevalid").value = 0
    dut.tse0_mtx_acpt.value = 1          # port-0 TX sink always accepts
    dut.tse1_mtx_acpt.value = 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 6)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 3)


async def monitor(dut, n):
    from cocotb.triggers import ReadOnly, RisingEdge
    last = None
    for _ in range(n):
        await ReadOnly()
        st = (int(dut.port0_mux.busy.value), int(dut.port0_mux.sel.value),
              int(dut.fwd0_rdy.value), int(dut.fwd0_eof.value),
              int(dut.cfq_rdy.value), int(dut.cfq_eof.value),
              int(dut.cfs_rdy.value), int(dut.cfs_eof.value),
              int(dut.tse0_mtx_rdy.value), int(dut.tse0_mtx_acpt.value))
        if st != last:
            dut._log.info("busy=%d sel=%d | fwd rdy=%d eof=%d | cfq rdy=%d eof=%d | cfs rdy=%d eof=%d | mtx rdy=%d acpt=%d" % st)
            last = st
        await RisingEdge(dut.clk)


@cocotb.test()
async def cert_frames_egress_port0(dut):
    await reset(dut)
    cocotb.start_soon(forwarded_load(dut))   # concurrent forwarded response traffic
    pass  # monitor off
    frames = []
    cur = bytearray()
    in_frame = False
    # run long enough for several windows (8 ticks * ~80 cyc * a few)
    for _ in range(40000):
        dut.tse0_mtx_acpt.value = 1
        await ReadOnly()
        rdy = int(dut.tse0_mtx_rdy.value)
        eof = int(dut.tse0_mtx_eof.value)
        dat = int(dut.tse0_mtx_dat.value)
        bv = int(dut.tse0_mtx_bytevalid.value)
        await RisingEdge(dut.clk)
        if rdy:
            for k in range(4 - bv):
                cur.append((dat >> (8 * k)) & 0xFF)
            in_frame = True
            if eof:
                frames.append(bytes(cur))
                cur = bytearray()
                in_frame = False
    cert_frames = [f for f in frames if f[0:6] == CERT_DST]
    dut._log.info("port0 frames=%d  cert frames=%d" % (len(frames), len(cert_frames)))
    if cert_frames:
        f = cert_frames[0]
        dut._log.info("first cert frame %dB: %s" % (len(f), f[:24].hex()))
        # payload starts after the 14-byte MAC header
        assert f[14:22] == b"ilock-v5", f"cert payload not ilock-v5: {f[14:22]!r}"
    assert len(cert_frames) > 0, f"NO cert frames egressed port0 (saw {len(frames)} frames total)"
    dut._log.info("cert_frames_egress_port0: %d cert frames out port0, payload ilock-v5" % len(cert_frames))


@cocotb.test()
async def beacon_frames_egress_port0(dut):
    """Tick beacon (design A clock broadcast) egresses port0 with DST 02:..:CB and
    payload 'ilbcn-v1'. No forwarded load here so the lowest-priority beacon isn't
    starved; run past STRIDE ticks (TICK_DIV=2048, STRIDE=16 -> ~tick 16)."""
    await reset(dut)
    BEACON_DST = bytes.fromhex("0200000000cb")
    frames = []
    cur = bytearray()
    for _ in range(50000):
        dut.tse0_mtx_acpt.value = 1
        await ReadOnly()
        rdy = int(dut.tse0_mtx_rdy.value)
        eof = int(dut.tse0_mtx_eof.value)
        dat = int(dut.tse0_mtx_dat.value)
        bv = int(dut.tse0_mtx_bytevalid.value)
        await RisingEdge(dut.clk)
        if rdy:
            for k in range(4 - bv):
                cur.append((dat >> (8 * k)) & 0xFF)
            if eof:
                frames.append(bytes(cur))
                cur = bytearray()
    beacon_frames = [f for f in frames if f[0:6] == BEACON_DST]
    dut._log.info("port0 frames=%d  beacon frames=%d" % (len(frames), len(beacon_frames)))
    assert len(beacon_frames) > 0, f"NO beacon frames egressed port0 (saw {len(frames)} total)"
    bf = beacon_frames[0]
    dut._log.info("first beacon %dB: %s" % (len(bf), bf[:24].hex()))
    assert bf[14:22] == b"ilbcn-v1", f"beacon payload not ilbcn-v1: {bf[14:22]!r}"
    dut._log.info("beacon_frames_egress_port0: %d beacon frames out, payload ilbcn-v1" % len(beacon_frames))
