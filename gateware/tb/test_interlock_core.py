"""G3-G5 conformance: interlock_core vs the Python golden model (prototype/
interlock.py + wire.py), HMAC deferred.

Drives the DUT and a Python `Interlock` with identical packet / bucket-tick /
nonce events; asserts each accept/drop decision agrees and the emitted 108-byte
certificate *body* equals the model's body (`ref_cert[:-TAG]`). One cert-body
equality covers hashing, the bucket/window fold, and cert assembly; the
accept/drop check covers the drop rules. The HMAC tag is added in a later stage,
at which point the comparison extends to the full certificate with no test change.
"""
import os
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "prototype"))
import wire as W
from interlock import Interlock

IID, N = 7, 8                 # must match the interlock_core defaults
SMAX, CAP = 100000, 100000
MAC = W.H(b"mac")
NONCE = W.H(b"nonce")[:16]


async def reset(dut):
    dut.s_valid.value = 0
    dut.s_data.value = 0
    dut.s_last.value = 0
    dut.s_dir.value = 0
    dut.nonce_valid.value = 0
    dut.nonce.value = 0
    dut.bucket_tick.value = 0
    dut.cert_ready.value = 1
    dut.mac_key.value = int.from_bytes(MAC, "big")
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 3)


async def wait_idle(dut):
    while not dut.idle.value:
        await RisingEdge(dut.clk)


async def latch_nonce(dut, nonce):
    dut.nonce.value = int.from_bytes(nonce, "big")
    dut.nonce_valid.value = 1
    await RisingEdge(dut.clk)
    dut.nonce_valid.value = 0


async def send_packet(dut, direction, pkt):
    dut.s_dir.value = 0 if direction == "in" else 1
    n = len(pkt)
    for i, b in enumerate(pkt):
        dut.s_data.value = b
        dut.s_last.value = 1 if i == n - 1 else 0
        dut.s_valid.value = 1
        while True:
            await ReadOnly()
            rdy = dut.s_ready.value
            await RisingEdge(dut.clk)
            if rdy:
                break
    dut.s_valid.value = 0
    dut.s_last.value = 0
    while not dut.pkt_done.value:
        await RisingEdge(dut.clk)
    return bool(dut.pkt_accepted.value)


async def tick(dut):
    """Pulse bucket_tick, then wait for the whole boundary to finish before
    returning (real ticks are 1 ms apart, so they never overlap a ~280-cycle
    boundary; the single tick_pending latch relies on that)."""
    await wait_idle(dut)
    dut.bucket_tick.value = 1
    await RisingEdge(dut.clk)
    dut.bucket_tick.value = 0
    while dut.idle.value:          # boundary starts
        await RisingEdge(dut.clk)
    while not dut.idle.value:      # ...and completes (incl. cert emit on the Nth)
        await RisingEdge(dut.clk)


def cert_collector(dut, box):
    """Background coroutine: capture the cert body whenever it streams out."""
    async def run():
        buf = bytearray()
        while True:
            await ReadOnly()
            if dut.cert_valid.value and dut.cert_ready.value:
                buf.append(int(dut.cert_data.value))
                if bool(dut.cert_last.value):
                    box["body"] = bytes(buf)
                    return
            await RisingEdge(dut.clk)
    return cocotb.start_soon(run())


async def run_window(dut, ref, schedule):
    """Co-drive N buckets; assert accept/drop agreement; return the DUT cert body."""
    box = {}
    cert_collector(dut, box)
    for i in range(N):
        await wait_idle(dut)
        for direction, pkt in schedule.get(i, []):
            accepted = await send_packet(dut, direction, pkt)
            model_accepted = ref.on_packet(direction, pkt) is not None
            assert accepted == model_accepted, \
                f"bucket {i} {direction}: dut={accepted} model={model_accepted}"
        await tick(dut)
        ref.on_bucket_boundary()
    while "body" not in box:
        await RisingEdge(dut.clk)
    ref_body = ref.on_second()[:-W.TAG]
    return box["body"], ref_body


def new_model():
    return Interlock(MAC, IID, s_max=SMAX, capacity=CAP, buckets_per_cert=N)


def pair(rid, prompt, key, resp):
    return (W.input_packet(rid, key, W.encrypt(key, b"in", W.tokens_to_bytes(prompt))),
            W.output_packet(rid, W.encrypt(key, b"out", W.tokens_to_bytes(resp))))


async def boot(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset(dut)
    ref = new_model()
    await latch_nonce(dut, NONCE)
    ref.on_nonce(NONCE)
    return ref


@cocotb.test()
async def honest_pair(dut):
    ref = await boot(dut)
    in_pkt, out_pkt = pair(1, [10, 20, 30], W.H(b"k"), [3681, 338, 278])
    body, ref_body = await run_window(dut, ref, {0: [("in", in_pkt)], 1: [("out", out_pkt)]})
    assert body == ref_body, f"\n dut={body.hex()}\n ref={ref_body.hex()}"
    dut._log.info("honest_pair: cert body matches (108 bytes)")


@cocotb.test()
async def drop_rules(dut):
    ref = await boot(dut)
    key = W.H(b"k")
    good = W.output_packet(5, W.encrypt(key, b"out", W.tokens_to_bytes([1])))
    stale = W.output_packet(4, W.encrypt(key, b"out", W.tokens_to_bytes([2])))  # id < last
    body, ref_body = await run_window(dut, ref, {0: [("out", good), ("out", stale)]})
    assert body == ref_body
    dut._log.info("drop_rules: non-monotonic id dropped, cert body matches")


@cocotb.test()
async def length_mismatch(dut):
    ref = await boot(dut)
    key = W.H(b"k")
    ok = W.output_packet(1, W.encrypt(key, b"out", W.tokens_to_bytes([7])))
    bad = bytearray(W.output_packet(2, W.encrypt(key, b"out", W.tokens_to_bytes([9, 9]))))
    bad[0:4] = (999).to_bytes(4, "big")   # declared length != actual ciphertext
    body, ref_body = await run_window(dut, ref, {0: [("out", ok), ("out", bytes(bad))]})
    assert body == ref_body
    dut._log.info("length_mismatch: bad-length packet dropped, cert body matches")


@cocotb.test()
async def multi_turn(dut):
    ref = await boot(dut)
    for rid in (1, 2, 3):
        in_pkt, out_pkt = pair(rid, [rid, rid + 1], W.H(b"k%d" % rid), [rid * 7, rid * 9])
        body, ref_body = await run_window(dut, ref, {0: [("in", in_pkt)], 1: [("out", out_pkt)]})
        assert body == ref_body, f"window {rid} mismatch"
    dut._log.info("multi_turn: 3 windows, cert bodies match")


@cocotb.test()
async def fuzz_buckets(dut):
    """Random multi-packet buckets spread across the window — exercises the
    running bucket hash across 64-byte block boundaries (44B records) and the
    window fold over non-empty buckets, both directions."""
    import random
    rng = random.Random(0xF1)
    in_rid = 0
    ref = await boot(dut)                     # one model + DUT, counters persist
    for w in range(3):                        # three windows
        schedule = {}
        for b in range(N):
            items = []
            out_rid = 0
            for _ in range(rng.randrange(0, 4)):          # 0-3 out packets, monotonic
                out_rid += rng.randrange(1, 3)
                ct = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 40)))
                items.append(("out", W.output_packet(out_rid, ct)))
            if rng.random() < 0.5:                        # maybe an in packet
                in_rid += rng.randrange(1, 3)
                ct = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 40)))
                items.insert(rng.randrange(0, len(items) + 1),
                             ("in", W.input_packet(in_rid, W.H(b"k"), ct)))
            if items:
                schedule[b] = items
        body, ref_body = await run_window(dut, ref, schedule)
        assert body == ref_body, f"fuzz window {w}: {body.hex()} != {ref_body.hex()}"
    dut._log.info("fuzz_buckets: 3 random windows, cert bodies match")
