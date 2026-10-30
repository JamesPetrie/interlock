"""The processes: `ilstack interlock | compute | frontend | verify | keygen`.

Keys live in a directory: `master.key` (shared by frontend and compute),
`mac.key` (interlock and verifier), `api.key` (clients and the verifier).
The protocol parameters are flags with the same defaults everywhere and
must agree across the three processes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import secrets
import sys
import time
import urllib.request

from . import packet as P
from .clock import SyncClock
from .crypto import PairKeys
from .dispatcher import Dispatcher, ProcessDispatcher
from .log import FileLog, MemoryLog
from .runtime import low_jitter, pacer_core

log = logging.getLogger("ilstack")


def _params(a) -> P.Params:
    return P.Params(num_slot=a.num_slot, bkt_num=a.bkt_num, bucket_ms=a.bucket_ms, device=a.device)


def _add_params(p: argparse.ArgumentParser) -> None:
    p.add_argument("--bucket-ms", type=float, default=1.0)
    p.add_argument("--bkt-num", type=int, default=1000)
    p.add_argument("--num-slot", type=int, default=64)
    p.add_argument("--device", type=int, default=0)
    p.add_argument("--keys", default="keys", help="directory with master.key, mac.key, api.key")


def _key(directory: str, name: str) -> bytes:
    with open(os.path.join(directory, name)) as f:
        return bytes.fromhex(f.read().strip())


def _clock(a):
    return SyncClock(a.bucket_ms, target=a.target, fill=a.fill, delta_unit=a.delta_unit)


async def _dispatcher(a, params, store, direction):
    """Who releases a bucket at its instant (--release): the pacer
    process on its own core, or the interface's queue from a kernel timer
    (`kernel`), or the NIC at a launch time (`hardware`); the last two need
    a raw link (--interlock raw:<iface>)."""
    host, port = _hostport(a.interlock)
    if a.release == "process":
        core = pacer_core(a.core)
        log.info("pacer process for the %s direction, core %s", "inbound" if direction == P.INBOUND else "outbound", core)
        return ProcessDispatcher(params, (host, port), store, direction, core=core,
                                 spin=a.spin_us / 1e6, max_slots=a.slots, ahead=a.ahead, link_mbps=a.link_mbps,
                                 target=a.target, pace=a.rate_limit, delta_unit=a.delta_unit, fill=a.fill)
    if not a.interlock.startswith("raw:"):
        raise SystemExit("kernel and hardware release need a raw link: --interlock raw:<iface>")
    from .links import CLOCK_MONOTONIC, CLOCK_TAI, check_queue
    from .wire import RawWire
    problem = check_queue(a.interlock[4:], a.release, params.num_slot, a.ahead)
    if problem:
        raise SystemExit(problem)
    low_jitter()                                                # no GC pauses, heap kept and locked: a fault costs frames at the FPGA
    hw = a.release == "hardware"                                # an offloaded etf's clock is CLOCK_TAI; fq's CLOCK_MONOTONIC
    wire = RawWire(a.interlock[4:], direction, CLOCK_TAI if hw else CLOCK_MONOTONIC)
    offset = time.clock_gettime(time.CLOCK_TAI) - time.monotonic() if hw else 0.0
    return Dispatcher(params, wire, _clock(a), store, direction, max_slots=a.slots, link_mbps=a.link_mbps,
                      release=a.release, ahead=a.ahead, rate_limit=a.rate_limit, tai_offset=offset)


def _hostport(s: str) -> tuple[str, int]:
    if s.startswith("raw:"):                      # a raw-frame link: the interface, no port
        return s, 0
    host, _, port = s.rpartition(":")
    return host or "0.0.0.0", int(port)


# --- keygen ------------------------------------------------------------------

def keygen(a) -> None:
    os.makedirs(a.keys, exist_ok=True)
    for name, n in (("master.key", 32), ("mac.key", 32), ("api.key", 16)):
        path = os.path.join(a.keys, name)
        if os.path.exists(path) and not a.force:
            print(f"{path} exists, keeping it")
            continue
        with open(path, "w") as f:
            f.write(secrets.token_bytes(n).hex() + "\n")
        os.chmod(path, 0o600)
        print(f"wrote {path}")


# --- interlock ---------------------------------------------------------------

async def interlock(a) -> None:
    from .interlock import SoftwareInterlock
    from .wire import TcpServerWire
    fe = await TcpServerWire.listen(*_hostport(a.listen_frontend))
    co = await TcpServerWire.listen(*_hostport(a.listen_compute))
    il = SoftwareInterlock(_params(a), fe, co, _key(a.keys, "mac.key"))
    await il.start()
    if a.core != "none":
        from .runtime import pin
        core = max(os.sched_getaffinity(0)) if a.core == "auto" and hasattr(os, "sched_getaffinity") else \
            (int(a.core) if a.core != "auto" else None)
        log.info("interlock: %s", pin(core))
    if a.spin:
        async def spinner():                         # keep the loop from ever blocking: packets and ticks are seen at once
            while True:
                await asyncio.sleep(0)
        asyncio.create_task(spinner())
    log.info("interlock: frontend port %s, compute port %s, bucket %g ms, %d buckets per certificate",
             a.listen_frontend, a.listen_compute, a.bucket_ms, a.bkt_num)
    try:
        while True:
            await asyncio.sleep(10)
            c = il.counters
            log.info("bucket %d, certificates %d, inbound accepted %d dropped %s, outbound accepted %d dropped %s",
                     il.closed, il.certificates_built, c[P.INBOUND].accepted, dict(c[P.INBOUND].dropped),
                     c[P.OUTBOUND].accepted, dict(c[P.OUTBOUND].dropped))
    finally:
        await il.stop()


# --- compute -----------------------------------------------------------------

async def compute(a) -> None:
    from .compute import ComputeNode
    from .engines import MockEngine, VllmEngine
    if a.engine == "mock":
        engine = MockEngine()
    else:
        engine = VllmEngine(a.model, tensor_parallel_size=a.tensor_parallel, gpu_memory_utilization=a.gpu_mem)
    params = _params(a)
    node = ComputeNode(params, await _dispatcher(a, params, MemoryLog(), P.OUTBOUND),
                       PairKeys(_key(a.keys, "master.key")), engine, concurrency=a.concurrency)
    await node.start()
    log.info("compute: %s engine, interlock at %s", a.engine, a.interlock)
    try:
        while True:
            await asyncio.sleep(10)
            log.info("compute %s dispatcher %s clock %s builder %s", vars(node.stats), vars(node.dispatcher.stats),
                     node.dispatcher.clock_stats(), node.dispatcher.builder_stats())
    finally:
        await node.stop()


# --- frontend ----------------------------------------------------------------

async def frontend(a) -> None:
    import uvicorn
    from .api import create_app
    from .frontend import Frontend
    from .tokenizers import HfTokenizer, MockTokenizer
    params = _params(a)
    tokenizer = MockTokenizer() if a.tokenizer == "mock" else HfTokenizer(a.tokenizer)
    store = FileLog(a.log_dir) if a.log_dir else MemoryLog()
    fe = Frontend(params, await _dispatcher(a, params, store, P.INBOUND),
                  PairKeys(_key(a.keys, "master.key")), store, timeout=a.timeout, retries=a.retries)
    await fe.start()
    keys = {_key(a.keys, "api.key").hex()} if a.api_keys == "keys" else set(filter(None, a.api_keys.split(",")))
    app = create_app(fe, tokenizer, a.model_name, keys, concurrency=a.concurrency)
    host, port = _hostport(a.http)
    log.info("frontend: interlock at %s, API on %s:%d", a.interlock, host, port)
    try:
        await uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="info")).serve()
    finally:
        await fe.stop()


# --- verify ------------------------------------------------------------------

def verify(a) -> int:
    from .verifier import Opening, Verifier
    api_key = a.api_key or _key(a.keys, "api.key").hex()

    def call(path, body=None):
        req = urllib.request.Request(a.frontend.rstrip("/") + path, method="POST" if body is not None else "GET",
                                     headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                                     data=None if body is None else json.dumps(body).encode())
        with urllib.request.urlopen(req, timeout=a.http_timeout) as r:
            return json.load(r)

    remote = call("/verifier/params")
    params = P.Params(remote["num_slot"], remote["bkt_num"], remote["bucket_ms"], remote["device"])
    v = Verifier(_key(a.keys, "mac.key"), params)
    certs = [P.Certificate.unpack(bytes.fromhex(h)) for h in call("/verifier/certificates")["certificates"]]
    valid = sum(c.verify(v.mac_key) for c in certs)
    print(f"{len(certs)} certificates stored by the prover, {valid} with a valid tag" +
          (f", buckets {certs[0].bkt_start}..{certs[-1].bkt_start + certs[-1].bkt_num - 1}" if certs else ""))
    for i in range(a.anchors):
        nonce = v.new_nonce()
        t0 = time.time()
        cert = P.Certificate.unpack(bytes.fromhex(call("/verifier/nonce", {"nonce": nonce.hex()})["certificate"]))
        res = v.anchor(nonce, cert, t0, time.time())
        print(f"anchor {i + 1}: certificate for buckets {cert.bkt_start}.. {'OK' if res['ok'] else res['checks']}")
        if not res["ok"]:
            return 1
    ok = 0
    for i in range(a.n):
        d, bucket, byte = v.select(a.window, P.OUTBOUND if a.direction == "outbound" else P.INBOUND,
                                   floor=min(c.bkt_start for c in certs) if certs else None)
        op = Opening.from_json(call("/verifier/open", {"dir": d, "bucket": bucket, "byte": byte}))
        res = v.check(op)
        ok += res["verified"]
        what = "empty" if op.hit.index is None else f"packet {res['id']} -> {res['reference']}"
        print(f"challenge {i + 1}: bucket {bucket} byte {byte}: {what}: "
              f"{'VERIFIED' if res['verified'] else 'FAILED ' + str({k: v for k, v in res['checks'].items() if not v})}")
    print(f"{ok}/{a.n} verified")
    return 0 if ok == a.n else 1


# --- timing tools --------------------------------------------------------------

def timing_stub(a) -> None:
    from .timing import run_stub
    host, port = _hostport(a.listen)
    core = None
    if a.core == "auto" and hasattr(os, "sched_getaffinity"):
        core = max(os.sched_getaffinity(0))
    elif a.core not in ("auto", "none"):
        core = int(a.core)
    run_stub(host, port, P.INBOUND if a.direction == "inbound" else P.OUTBOUND, a.bucket_ms, core, a.trace)


async def timing_send(a) -> None:
    from .timing import run_send
    host, port = _hostport(a.stub)
    core = pacer_core(a.core) if a.release == "process" else None
    params = P.Params(num_slot=a.num_slot, bkt_num=a.bkt_num, bucket_ms=a.bucket_ms)
    result = await run_send(host, port, params, a.slots, a.buckets, a.target, core=core, spin=a.spin_us / 1e6, ahead=a.ahead,
                            link_mbps=a.link_mbps, delta_unit=a.delta_unit, fill=a.fill, ahead_msgs=a.in_flight,
                            release=a.release, rate_limit=a.rate_limit)
    print(json.dumps(result, indent=1))


def timing_recv(a) -> None:
    from .timing import run_recv
    core = None
    if a.core == "auto" and hasattr(os, "sched_getaffinity"):
        core = max(os.sched_getaffinity(0))
    elif a.core not in ("auto", "none"):
        core = int(a.core)
    print(json.dumps(run_recv(a.iface, a.bucket_ms, a.seconds, core), indent=1))


# --- main --------------------------------------------------------------------

def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="ilstack")
    sub = p.add_subparsers(dest="cmd", required=True)

    k = sub.add_parser("keygen", help="write master.key, mac.key and api.key")
    k.add_argument("keys", nargs="?", default="keys")
    k.add_argument("--force", action="store_true")

    i = sub.add_parser("interlock", help="the software interlock")
    _add_params(i)
    i.add_argument("--listen-frontend", default="0.0.0.0:7001")
    i.add_argument("--listen-compute", default="0.0.0.0:7002")
    i.add_argument("--spin", action="store_true", help="busy-poll instead of sleeping (a core's worth of CPU, much less wake-up latency)")
    i.add_argument("--core", default="auto", help="core to pin the interlock to: a number, auto, or none")

    c = sub.add_parser("compute", help="the compute node")
    _add_params(c)
    c.add_argument("--interlock", default="127.0.0.1:7002")
    c.add_argument("--release", choices=["process", "kernel", "hardware"], default="process",
                   help="who releases a bucket at its instant: the pacer process, the interface's fq queue from a kernel "
                        "timer (raw link, CLOCK_MONOTONIC), or the NIC at a launch time (etf offload, CLOCK_TAI)")
    c.add_argument("--rate-limit", action="store_true", help="a link faster than the device behind it: the pacer sends frame by frame at the wire "
                   "time, the kernel release stamps every frame with its own time (default: one hand-over, one stamp per bucket)")
    c.add_argument("--core", default="auto", help="core to pin the pacer to: a number, auto, or none")
    c.add_argument("--spin-us", type=float, default=50, help="the pacer busy-waits this long before each transmit instant")
    c.add_argument("--slots", type=int, default=None, help="packets per bucket (default: what fits the bucket's send window)")
    c.add_argument("--ahead", type=int, default=1, help="buckets built ahead of the one the clock can reach now: the lead the kernel release hands over with, "
                   "the margin beyond the pacer's next bucket for the process release")
    c.add_argument("--link-mbps", type=float, default=1000, help="the wire's rate: bounds the packets per bucket")
    c.add_argument("--target", type=float, default=0.25, help="where in the bucket the first packet should land (fraction)")
    c.add_argument("--fill", type=float, default=0.85, help="how far into a bucket packets may still be sent for it")
    c.add_argument("--delta-unit", type=float, default=1e-6, help="the interlock's DELTA unit in seconds (1e-8 on the FPGA)")
    c.add_argument("--engine", choices=["mock", "vllm"], default="mock")
    c.add_argument("--model", default="Qwen/Qwen2.5-0.5B-Instruct")
    c.add_argument("--tensor-parallel", type=int, default=1)
    c.add_argument("--gpu-mem", type=float, default=0.5)
    c.add_argument("--concurrency", type=int, default=16)

    f = sub.add_parser("frontend", help="the frontend node with its HTTP API")
    _add_params(f)
    f.add_argument("--interlock", default="127.0.0.1:7001")
    f.add_argument("--release", choices=["process", "kernel", "hardware"], default="process",
                   help="who releases a bucket at its instant: the pacer process, the interface's fq queue from a kernel "
                        "timer (raw link, CLOCK_MONOTONIC), or the NIC at a launch time (etf offload, CLOCK_TAI)")
    f.add_argument("--rate-limit", action="store_true", help="a link faster than the device behind it: the pacer sends frame by frame at the wire "
                   "time, the kernel release stamps every frame with its own time (default: one hand-over, one stamp per bucket)")
    f.add_argument("--core", default="auto", help="core to pin the pacer to: a number, auto, or none")
    f.add_argument("--spin-us", type=float, default=50, help="the pacer busy-waits this long before each transmit instant")
    f.add_argument("--slots", type=int, default=None, help="packets per bucket (default: what fits the bucket's send window)")
    f.add_argument("--ahead", type=int, default=1, help="buckets built ahead of the one the clock can reach now: the lead the kernel release hands over with, "
                   "the margin beyond the pacer's next bucket for the process release")
    f.add_argument("--link-mbps", type=float, default=1000, help="the wire's rate: bounds the packets per bucket")
    f.add_argument("--target", type=float, default=0.25, help="where in the bucket the first packet should land (fraction)")
    f.add_argument("--fill", type=float, default=0.85, help="how far into a bucket packets may still be sent for it")
    f.add_argument("--delta-unit", type=float, default=1e-6, help="the interlock's DELTA unit in seconds (1e-8 on the FPGA)")
    f.add_argument("--tokenizer", default="mock", help="mock or a HuggingFace model name")
    f.add_argument("--model-name", default="mock")
    f.add_argument("--api-keys", default="keys", help="'keys' for api.key, or a comma-separated list")
    f.add_argument("--http", default="0.0.0.0:8000")
    f.add_argument("--log-dir", default=None, help="prover log directory (default: in memory)")
    f.add_argument("--timeout", type=float, default=60.0)
    f.add_argument("--retries", type=int, default=1)
    f.add_argument("--concurrency", type=int, default=32)

    v = sub.add_parser("verify", help="anchor, challenge and verify against a frontend")
    v.add_argument("--frontend", default="http://127.0.0.1:8000")
    v.add_argument("--api-key", default=None, help="default: api.key from --keys")
    v.add_argument("--keys", default="keys", help="directory with mac.key")
    v.add_argument("--anchors", type=int, default=1)
    v.add_argument("-n", type=int, default=8, help="challenges")
    v.add_argument("--window", type=int, default=100000, help="buckets back from the latest certified one")
    v.add_argument("--direction", choices=["outbound", "inbound"], default="outbound")
    v.add_argument("--http-timeout", type=float, default=60.0)

    ts = sub.add_parser("timing-stub", help="receive and time one sender's buckets, send syncs; nothing else")
    ts.add_argument("--listen", default="0.0.0.0:17001")
    ts.add_argument("--direction", choices=["inbound", "outbound"], default="inbound")
    ts.add_argument("--bucket-ms", type=float, default=1.0)
    ts.add_argument("--core", default="auto")
    ts.add_argument("--trace", default=None, help="per-packet trace file (local disk)")

    tx = sub.add_parser("timing-send", help="send buckets of dummy packets to a timing stub through the pacer")
    tx.add_argument("--stub", default="127.0.0.1:17001")
    tx.add_argument("--bucket-ms", type=float, default=1.0)
    tx.add_argument("--bkt-num", type=int, default=1000)
    tx.add_argument("--num-slot", type=int, default=64)
    tx.add_argument("--slots", type=int, default=32, help="packets per bucket")
    tx.add_argument("--buckets", type=int, default=1000, help="how many buckets to send")
    tx.add_argument("--target", type=float, default=0.1)
    tx.add_argument("--release", choices=["process", "kernel", "hardware"], default="process",
                   help="who releases a bucket at its instant: the pacer process, the interface's fq queue from a kernel "
                        "timer (raw link, CLOCK_MONOTONIC), or the NIC at a launch time (etf offload, CLOCK_TAI)")
    tx.add_argument("--rate-limit", action="store_true", help="a link faster than the device behind it: the pacer sends frame by frame at the wire "
                   "time, the kernel release stamps every frame with its own time (default: one hand-over, one stamp per bucket)")
    tx.add_argument("--core", default="auto")
    tx.add_argument("--spin-us", type=float, default=50)
    tx.add_argument("--ahead", type=int, default=1)
    tx.add_argument("--link-mbps", type=float, default=1000, help="the receiver's rate: packets are spaced at its wire time")
    tx.add_argument("--delta-unit", type=float, default=1e-6, help="the interlock's timer unit in seconds (FPGA: 1e-8)")
    tx.add_argument("--fill", type=float, default=0.85, help="how far into a bucket packets may still leave (fraction)")
    tx.add_argument("--in-flight", type=int, default=1, help="buckets handed to the pacer ahead of the one being sent")

    tr = sub.add_parser("timing-recv", help="count the frames the interlock forwards onto an interface, per bucket")
    tr.add_argument("--iface", required=True)
    tr.add_argument("--bucket-ms", type=float, default=1.0)
    tr.add_argument("--seconds", type=float, default=30)
    tr.add_argument("--core", default="auto")

    a = p.parse_args(argv)
    if a.cmd == "keygen":
        keygen(a)
    elif a.cmd == "verify":
        sys.exit(verify(a))
    elif a.cmd == "timing-stub":
        try:
            timing_stub(a)
        except KeyboardInterrupt:
            pass
    elif a.cmd == "timing-recv":
        timing_recv(a)
    else:
        try:
            asyncio.run({"interlock": interlock, "compute": compute, "frontend": frontend,
                         "timing-send": timing_send}[a.cmd](a))
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
