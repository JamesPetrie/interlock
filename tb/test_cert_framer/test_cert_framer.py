"""cert_framer: a 140-byte certificate byte stream must come out as a canonical
MAC-TX frame addressed to the verifier — [CERT_DST][CERT_SRC][LEN=140][cert][FCS]."""
import zlib

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge, Combine

CERT_DST = 0x02_00_00_00_00_CE
CERT_SRC = 0x02_00_00_00_00_CF
CERT_LEN = 140


def expected_frame(cert: bytes) -> bytes:
    body = (CERT_DST.to_bytes(6, "big") + CERT_SRC.to_bytes(6, "big")
            + len(cert).to_bytes(2, "big") + cert)
    return body + zlib.crc32(body).to_bytes(4, "little")


async def reset(dut):
    cocotb.start_soon(Clock(dut.clk, 8, unit="ns").start())
    dut.c_valid.value = 0
    dut.c_data.value = 0
    dut.c_last.value = 0
    dut.out_acpt.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)


async def drive_cert(dut, cert):
    for i, b in enumerate(cert):
        dut.c_data.value = b
        dut.c_last.value = 1 if i == len(cert) - 1 else 0
        dut.c_valid.value = 1
        while True:
            await ReadOnly()
            rdy = int(dut.c_ready.value)
            await RisingEdge(dut.clk)
            if rdy:
                break
    dut.c_valid.value = 0
    dut.c_last.value = 0


async def sink_frame(dut, throttle=0.0):
    import random
    rng = random.Random(3)
    got = bytearray()
    started = False
    while True:
        dut.out_acpt.value = 0 if rng.random() < throttle else 1
        await ReadOnly()
        rdy = int(dut.out_rdy.value)
        acpt = int(dut.out_acpt.value)
        dat = int(dut.out_dat.value)
        eof = int(dut.out_eof.value)
        bv = int(dut.out_bytevalid.value)
        await RisingEdge(dut.clk)
        if rdy and acpt:
            started = True
            for k in range(4 - bv):
                got.append((dat >> (8 * k)) & 0xFF)
            if eof:
                break
        elif started and not rdy:
            pass
    dut.out_acpt.value = 0
    return bytes(got)


@cocotb.test()
async def frame_a_cert(dut):
    await reset(dut)
    cert = bytes((i * 7 + 3) & 0xFF for i in range(CERT_LEN))
    cert = b"ilock-v5" + cert[8:]                 # mimic a real cert prefix
    rx = cocotb.start_soon(sink_frame(dut))
    tx = cocotb.start_soon(drive_cert(dut, cert))
    await Combine(tx, rx)
    got = rx.result()
    exp = expected_frame(cert)
    assert got == exp, (f"cert frame mismatch len {len(got)} vs {len(exp)}\n"
                        f" got={got[:20].hex()}\n exp={exp[:20].hex()}")
    dut._log.info("frame_a_cert: 140B cert -> %dB verifier frame, FCS OK" % len(got))


@cocotb.test()
async def frame_a_cert_throttled(dut):
    await reset(dut)
    cert = b"ilock-v5" + bytes(range(132))
    rx = cocotb.start_soon(sink_frame(dut, throttle=0.4))
    tx = cocotb.start_soon(drive_cert(dut, cert))
    await Combine(tx, rx)
    assert rx.result() == expected_frame(cert), "throttled cert frame mismatch"
    dut._log.info("frame_a_cert_throttled: backpressure-safe")
