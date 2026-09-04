"""GPU back-end for the interlock model server (runs on the Spark host).

The server splits in two because the two halves need incompatible privileges on
this box:

  * the wire half needs CAP_NET_RAW + CAP_SYS_NICE for raw L2 and RT scheduling,
    and the Spark has no sudo -- so it has to run in a container;
  * the GPU half needs the host's validated CUDA venv (torch built for sm_121,
    VerInf's JIT'd primitives, the prebuilt Rust verifier).

Rather than rebuild that CUDA stack inside a container, the wire half stays a
slim container and talks to this process over loopback (`--network host` puts
them in the same netns). Keeping generation and proving off the real-time thread
is good hygiene besides: a multi-minute proof must never stall the flywheel.

Protocol: newline-delimited JSON, one request per connection.

    {"op":"generate","ids":[...]}          -> {"ids":[...]}
    {"op":"challenge","req":[...],
     "rsp":[...], "tq":"80"}               -> {"status":"..."}    (zero or more)
                                              {"result":{...}}    (exactly one)

Run:
    VERINF=../../VerInf MODEL_DIR=$VERINF/models/TinyLlama-1.1B-Chat-v1.0 \\
      $VERINF/venv/bin/python model_backend.py
"""
import json
import os
import select
import socket
import struct
import re
import subprocess
import sys
import threading
import time

HOST, PORT = "127.0.0.1", int(os.environ.get("BACKEND_PORT", "9917"))
_APP = os.path.dirname(os.path.abspath(__file__))
# VerInf as a sibling checkout of the interlock repo; VERINF overrides.
VERINF = os.environ.get("VERINF") or os.path.join(
    os.path.dirname(os.path.dirname(_APP)), "VerInf")
MODEL_DIR = os.environ.get("MODEL_DIR", os.path.join(VERINF, "models/llama-3.2-1b"))
CHALLENGE_PY = os.environ.get(
    "CHALLENGE_PY",
    "%s/venv/bin/python -u %s/analysis/interlock_challenge.py" % (VERINF, VERINF))

_M = {"tok": None, "model": None}
# Serialized: two requests racing an empty cache would both load 2.2 GB of
# weights onto the GPU, and the loser's copy leaks VRAM for the process's life.
_M_LOCK = threading.Lock()


def load_model():
    with _M_LOCK:
        if _M["model"] is None:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
            print("[backend] loading %s ..." % MODEL_DIR, flush=True)
            _M["tok"] = AutoTokenizer.from_pretrained(MODEL_DIR)
            _M["model"] = AutoModelForCausalLM.from_pretrained(
                MODEL_DIR, dtype=torch.bfloat16).to("cuda").eval()
            print("[backend] model ready", flush=True)
    return _M["tok"], _M["model"]


def generate(in_ids):
    """Greedy (argmax) continuation. Pure argmax is what drives the proof's
    unexplained-information bound to ~0, so do_sample stays off."""
    import torch
    tok, model = load_model()
    # Reject out-of-range ids BEFORE they reach the embedding lookup. Anything on the
    # wire can land here -- a stray bring-up probe parses as token ids just fine -- and
    # an out-of-range index triggers a device-side assert that poisons the CUDA context
    # for every later request, not just the bad one. Fail this request instead.
    vocab = int(getattr(model.config, "vocab_size", 0)) or len(tok)
    bad = [t for t in in_ids if not (0 <= int(t) < vocab)]
    if bad:
        raise ValueError("token ids out of range for vocab %d: %s%s"
                         % (vocab, bad[:4], " ..." if len(bad) > 4 else ""))
    k = int(os.environ.get("MAX_NEW_TOKENS", "24"))
    ids = torch.tensor([in_ids], dtype=torch.long, device="cuda")
    with torch.no_grad():
        out = model.generate(ids, max_new_tokens=k, do_sample=False, num_beams=1,
                             pad_token_id=tok.eos_token_id)
    new_ids = out[0, len(in_ids):].tolist()
    # Stop at the end of the answer's line. The demo prompt is completion-style
    # ("Question: ...\nAnswer:") and a base-style continuation, having finished
    # the answer, happily invents the NEXT question -- "...Atomic Energy Agency.
    # Question: What is the purpose of the IAEA?" -- until max_new_tokens runs
    # out, because EOS never comes. The first newline after real text IS the
    # stop token. Leading newlines (the model clearing its throat) don't count.
    seen_text, cut = False, None
    for i, t in enumerate(new_ids):
        piece = tok.decode([t])
        if "\n" in piece:
            if seen_text:
                cut = i
                break
        elif piece.strip():
            seen_text = True
    if cut:
        new_ids = new_ids[:cut]
    print("[backend] generate: %d in -> %d out" % (len(in_ids), len(new_ids)), flush=True)
    return new_ids


def challenge(conn, req_ids, rsp_ids, tq, crypto=None, seed=None):
    """Prove + verify, streaming progress lines back as they appear.

    `crypto` (when the wire ran encrypted) carries the key-binding inputs:
    KEY_COMMIT and both ciphertexts are public, and the nonce lets the prover
    re-derive the per-request key from its own copy of the PSK. The key is never
    put on this socket or in the child's argv -- argv is world-readable in /proc,
    and a demo that leaks its own session key on the process table would make the
    binding it is demonstrating meaningless."""
    # `base` is the interpreter + flags + script; `args` is the challenge's own
    # argv. They stay separate because the worker has to substitute itself for
    # the script while keeping the interpreter and passing args as a job.
    base = CHALLENGE_PY.split()
    args = ["--request", ",".join(map(str, req_ids)),
            "--response", ",".join(map(str, rsp_ids)),
            "--t-queries", str(tq)]
    if crypto:
        args += ["--nonce", crypto["nonce"], "--key-commit", crypto["key_commit"],
                 "--ct-in", crypto["ct_in"], "--ct-out", crypto["ct_out"]]
    # The seed is public (it is the challenge), so argv is fine for it -- unlike
    # the key material above, which is deliberately kept off the process table.
    if seed:
        args += ["--seed", seed]
    print("[backend] challenge: req=%d rsp=%d tq=%s%s"
          % (len(req_ids), len(rsp_ids), tq,
             " +key-binding" if crypto else ""), flush=True)
    # interlock_challenge.py selects its model with VERINF_MODEL, while this
    # process generates with MODEL_DIR. If they disagree the prover proves a
    # model that never produced this response, so pin the child to ours.
    child_env = dict(os.environ, VERINF_MODEL=MODEL_DIR)
    lines = _run_challenge(base, args, child_env, str(tq))
    result = None
    for line in lines:
        line = line.rstrip()
        if line.startswith("CHALLENGE_RESULT"):
            result = {}
            for kv in line.split()[1:]:
                k, _, v = kv.partition("=")
                result[k] = v
        elif line.strip():
            # A failing prover is otherwise invisible. model_server throttles status
            # lines to one every few seconds, so a traceback is dropped on the floor
            # and the demo reports a bare verify=ERROR with the cause recorded
            # nowhere -- which cost an hour of bisecting after a reboot. The backend's
            # own log is not throttled, so keep error-shaped lines here too.
            if _ERR_RE.search(line):
                print("[backend] prover: %s" % line[:200], flush=True)
            _prover_log(line)
            send(conn, {"status": line[:120]})
    send(conn, {"result": result or {"verdict": "FAIL", "verify": "ERROR"}})


_ERR_RE = re.compile(r"traceback|error|exception|assert|no such file|not found", re.I)
# The prover's full transcript, unthrottled. model_server samples status lines for
# the wire (one every few seconds) and the UI shows fewer still, so the only record
# of WHY a proof rejected was previously nowhere at all.
_PROVER_LOG = os.environ.get("PROVER_LOG", "/tmp/interlock-logs/prover.log")
# The directory only existed if bringup.sh had run; after a clean systemd boot it
# did not, the OSError below swallowed every write, and the one file that
# explains a REJECT silently failed to exist. Make it, once, on import.
try:
    os.makedirs(os.path.dirname(_PROVER_LOG), exist_ok=True)
except OSError:
    pass


def _prover_log(line):
    try:
        with open(_PROVER_LOG, "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- prover worker
# Spawning CHALLENGE_PY per challenge cost ~1.2 s of interpreter start, torch
# import and CUDA context creation before any proving began. A resident worker
# pays that once. It is a pure latency optimization: if the worker is missing,
# fails to start, or dies mid-job, we fall back to the original one-shot
# subprocess, so the demo cannot be broken by it -- only slowed to what it was.
_WORKER = {"proc": None, "tq": None}
WORKER_PY = os.path.join(VERINF, "analysis/challenge_worker.py")

# The wedge budget. A prover that hangs (a CUDA deadlock does not crash, it just
# stops) used to wedge this whole backend with it: the job read blocked forever,
# the UI watchdog could report the hang but nothing could clear it, and the next
# challenge queued into the same dead worker. Every read below now carries two
# deadlines -- silence, and total -- and blowing either one KILLS the process.
# A healthy prover prints constantly (sweep progress, verify progress), so five
# minutes of silence is a wedge, not a slow phase; the absolute cap covers a
# prover that babbles while going nowhere. Both are generous next to the ~15 s
# fast mode actually takes, because killing a real production-soundness proof
# (tq=80, ~6 min with progress the whole way) would be the worse bug.
CHALLENGE_INACT_S = float(os.environ.get("CHALLENGE_INACTIVITY_S", "300"))
CHALLENGE_MAX_S = float(os.environ.get("CHALLENGE_MAX_S", "1800"))
WORKER_READY_S = float(os.environ.get("WORKER_READY_S", "300"))


class _Wedged(Exception):
    pass


def _pump(proc, inactivity_s, max_s):
    """Yield stdout lines from `proc`, enforcing both deadlines.

    fd-level reads with select, never the TextIOWrapper: a buffered readline
    blocks with no way to arm a timeout, which is exactly how the last wedge
    propagated. Returns when the stream closes; raises _Wedged on a deadline,
    and the CALLER owns killing the process."""
    fd = proc.stdout.fileno()
    buf = b""
    t0 = last = time.time()
    while True:
        now = time.time()
        if now - last > inactivity_s:
            raise _Wedged("no prover output for %.0fs" % (now - last))
        if now - t0 > max_s:
            raise _Wedged("prover exceeded %.0fs total" % max_s)
        r, _, _ = select.select([fd], [], [], 1.0)
        if not r:
            continue
        chunk = os.read(fd, 65536)
        if not chunk:                            # stream closed (exit or kill)
            if buf:
                yield buf.decode("utf-8", "replace")
            return
        last = time.time()
        buf += chunk
        while b"\n" in buf:
            line, _, buf = buf.partition(b"\n")
            yield line.decode("utf-8", "replace")


def _kill(proc):
    try:
        proc.kill()
        proc.wait(timeout=5)
    except Exception:
        pass


def _worker_dead():
    p = _WORKER["proc"]
    return p is None or p.poll() is not None


def _stop_worker():
    p = _WORKER["proc"]
    if p is not None and p.poll() is None:
        try:
            p.stdin.close()
            p.wait(timeout=5)
        except Exception:
            p.kill()
    _WORKER["proc"] = None


def _start_worker(base, env, tq):
    """`base` is CHALLENGE_PY split: interpreter, flags, then the script last."""
    if not os.path.exists(WORKER_PY):
        return False
    script = base[-1]
    try:
        proc = subprocess.Popen(list(base[:-1]) + [WORKER_PY, script],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, bufsize=0, env=env)
    except OSError as e:
        print("[backend] worker spawn failed (%s); using one-shot" % e, flush=True)
        return False
    try:
        for line in _pump(proc, WORKER_READY_S, WORKER_READY_S):
            line = line.rstrip()
            if line == "WORKER_READY":
                _WORKER["proc"], _WORKER["tq"] = proc, tq
                return True
            if line:
                print("[backend] %s" % line, flush=True)
    except _Wedged as e:
        print("[backend] worker hung during startup (%s); killing it" % e, flush=True)
        _kill(proc)
    print("[backend] worker did not become ready; using one-shot", flush=True)
    return False


def _run_challenge(base, args, env, tq):
    """Yield the challenge's output lines, via the resident worker when possible.

    The worker binds LIGERO_T_QUERIES at its first job (demo_llama7b reads it at
    import), so a change in query count means a respawn rather than a silently
    stale setting."""
    if os.environ.get("CHALLENGE_WORKER", "1") != "1":
        return _run_challenge_oneshot(base + args, env)
    if not _worker_dead() and _WORKER["tq"] != tq:
        print("[backend] t-queries changed %s -> %s; respawning worker"
              % (_WORKER["tq"], tq), flush=True)
        _stop_worker()
    if _worker_dead() and not _start_worker(base, env, tq):
        return _run_challenge_oneshot(base + args, env)

    proc = _WORKER["proc"]
    out = []
    try:
        proc.stdin.write((json.dumps({"argv": args}) + "\n").encode())
        proc.stdin.flush()
        for line in _pump(proc, CHALLENGE_INACT_S, CHALLENGE_MAX_S):
            line = line.rstrip()
            if line.startswith("WORKER_DONE"):
                return out
            out.append(line)
    except _Wedged as e:
        # Kill, report, and do NOT retry: a wedge is a hung GPU or a deadlocked
        # worker, not a transient, and a one-shot retry would spend minutes
        # arriving at the same hang. The caller sees no CHALLENGE_RESULT line
        # and emits a FAIL/ERROR verdict, so the demo gets an answer instead of
        # a watchdog message about a backend nobody can unstick.
        print("[backend] prover WEDGED (%s); killing worker" % e, flush=True)
        _kill(proc)
        _WORKER["proc"] = None
        out.append("[backend] prover killed: %s" % e)
        return out
    except (BrokenPipeError, OSError) as e:
        print("[backend] worker died (%s); falling back to one-shot" % e, flush=True)
    _stop_worker()
    return _run_challenge_oneshot(base + args, env)


def _run_challenge_oneshot(cmd, env):
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            bufsize=0, env=env)
    out = []
    try:
        for line in _pump(proc, CHALLENGE_INACT_S, CHALLENGE_MAX_S):
            out.append(line.rstrip())
        proc.wait(timeout=10)
    except (_Wedged, subprocess.TimeoutExpired) as e:
        print("[backend] one-shot prover WEDGED (%s); killing it" % e, flush=True)
        _kill(proc)
        out.append("[backend] prover killed: %s" % e)
    return out


def send(conn, obj):
    conn.sendall((json.dumps(obj) + "\n").encode())


def handle(conn):
    try:
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = conn.recv(65536)
            if not chunk:
                return
            buf += chunk
        req = json.loads(buf.decode())
        if req["op"] == "generate":
            send(conn, {"ids": generate(req["ids"])})
        elif req["op"] == "challenge":
            challenge(conn, req["req"], req["rsp"], req.get("tq", "80"),
                      crypto=req.get("crypto"), seed=req.get("seed"))
        else:
            send(conn, {"error": "unknown op %r" % req.get("op")})
    except Exception as e:                       # never take the backend down
        print("[backend] error: %s: %s" % (type(e).__name__, e), flush=True)
        try:
            send(conn, {"error": "%s: %s" % (type(e).__name__, e)})
        except OSError:
            pass
    finally:
        conn.close()


def main():
    # Bind BEFORE the preload. Loading takes ~30 s (longer on a cold page cache,
    # i.e. exactly at boot), and with the bind after it there was a window where
    # the wire half's connect was refused outright: the request died with "no
    # response returned" while every health indicator showed green. Bound first,
    # an early request sits in the accept queue and simply waits out the load --
    # slow beats wrong. load_model() itself is serialized, so the queued
    # requests cannot double-load.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((HOST, PORT))
    s.listen(4)
    print("[backend] listening on %s:%d (model=%s)" % (HOST, PORT, MODEL_DIR), flush=True)
    if os.environ.get("PRELOAD_MODEL", "1") == "1":
        load_model()
    while True:
        conn, _ = s.accept()
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
