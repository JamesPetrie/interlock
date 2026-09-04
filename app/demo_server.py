#!/usr/bin/env python3
"""Spark half of the web demo: orchestrator, Socket.IO hub, and static host.

SHAPE. Two namespaces on one server. `/agent` is the Pi -- exactly one client,
dialing out, holding the certified wire. `/demo` is every browser. The server
owns the beat sequence and relays the Pi's events to the browsers; the browsers
never talk to the Pi.

    browser ──/demo──▶ demo_server ──/agent──▶ pi_agent ══ certified wire ══▶ Spark

WHAT IS AND IS NOT ON THIS LINK. The control link carries commands and metadata.
The prompt and the response cross the certified wire; pi_agent's `_scrub` is what
keeps everything else off the control plane. The one plaintext that comes back is
the decrypted response, emitted by the Pi because the Pi is what holds the key --
which is the point the demo is making, so it is a feature that it cannot come
from here.

MODE. Which prover runs, and at what strength, is deployment config that lives
in the backend service and the ilk_server container, not here; MODE_LABEL (see
below) is how this server is told what to call it, and it rides on every result
so the UI cannot render a verdict without it. As deployed the demo runs the
FAST prover (subsample_challenge.py, tq=1): ~30 s a run, the key binding and
the weld at full strength, the forward pass spot-checked at one (token, layer)
of ~460. Point CHALLENGE_PY at interlock_challenge.py for the full sound proof
(~3.5 min), and relabel to match.

Run:  VerInf/venv/bin/python -u demo_server.py --port 8770
"""
import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import time

import socketio
import uvicorn

HERE = os.path.dirname(os.path.abspath(__file__))
# The SvelteKit dashboard (adapted from sg-ai-safety-hub/inference-verification:
# Tailwind v4 + shadcn-svelte, adapter-static in SPA mode), served straight out of
# its own build directory -- no second copy to keep in sync. Build it with
#     cd dashboard && pnpm install && pnpm build
UI_DIR = os.path.join(HERE, os.environ.get("UI", "dashboard/build"))

MODEL_DIR = os.environ.get(
    "MODEL_DIR", "/home/spark/v2/VerInf/models/TinyLlama-1.1B-Chat-v1.0")


def model_fingerprint(path=MODEL_DIR):
    """SHA-256 of the weight file the prover actually runs.

    The demo's third box has to be a real digest of real bytes. It is NOT a
    registry lookup: nothing here checks this value against an externally
    pre-registered one, so the UI calls it the model's fingerprint and does not
    claim it was approved in advance. What the proof does establish is that the
    forward pass ran on the weights it committed to; comparing that commitment
    against a published one is the piece that is still missing."""
    f = os.path.join(path, "model.safetensors")
    if not os.path.exists(f):
        return None
    h = hashlib.sha256()
    with open(f, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


MODEL_FP = None      # filled at startup; 2.2 GB takes a few seconds

# What the pipeline behind this server actually runs is decided elsewhere -- the
# prover script by CHALLENGE_PY in interlock-backend.service, the opened columns
# by CHALLENGE_TQ in the ilk_server container -- so this server cannot derive the
# label and the deployment has to say it (MODE_LABEL in interlock-demo.service,
# kept next to those two knobs' cross-references). The default UNDER-claims: a
# stale label calling a sound run a spot check wastes credit, but the reverse
# would print a full-proof banner over a spot check, and that is the one lie the
# whole app is built to never tell.
MODE_LABEL = os.environ.get("MODE_LABEL") or (
    "spot check - 1 token-layer of ~460 at tq=1, NOT a proof of the "
    "forward pass")

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")


class State:
    """Single-flight, by construction. commit.bucket_hash raises on a bucket
    holding more than one packet, and the app only avoids that by keeping one
    packet on the wire at a time -- so two overlapping prompts do not degrade,
    they throw. The Pi enforces this too; doing it here as well means the UI can
    say 'busy' instead of surfacing a traceback."""
    def __init__(self):
        self.agent = None          # sid of the one Pi
        self.running = False
        self.rid = None
        self.t0 = None
        self.log = []
        self.watchdog = None
        self.wire_ok = False      # set once the Pi reports a locked flywheel
        self.rewarm = None
        # The hardware button panel toggles this; a panel PROMPT press runs
        # whatever it says. Deliberately NOT touched by reset(): the workload
        # choice is an operator setting, not part of a run.
        self.workload = "honest"

    def reset(self):
        self.running, self.rid, self.t0 = False, None, None
        self.log = []
        if self.watchdog is not None:
            self.watchdog.cancel()
            self.watchdog = None


S = State()


# Fast mode proves in ~15 s and sound mode in ~110 s. If nothing has come back
# well past that, something died -- the prover, the wire, the backend -- and the
# UI would otherwise sit on "proving" forever with the buttons disabled and no way
# back except restarting the server. Say so instead and re-enable the controls.
PROOF_TIMEOUT = float(os.environ.get("PROOF_TIMEOUT", "420"))


async def _proof_watchdog(rid):
    try:
        await asyncio.sleep(PROOF_TIMEOUT)
    except asyncio.CancelledError:
        return
    if S.running and S.rid == rid:
        print("[demo] proof watchdog fired for rid=%s" % rid, flush=True)
        await to_demo("beat:error", {
            "error": "no verdict after %ds -- check the prover on the Spark "
                     "(docker logs ilk_server) and that the board is still "
                     "emitting sync" % int(PROOF_TIMEOUT)})
        S.reset()


async def to_demo(event, data=None):
    """Fan out to browsers and keep a replay buffer, so a browser that connects
    or refreshes mid-run does not see a blank screen."""
    payload = dict(data or {})
    payload.setdefault("t", round(time.time() - S.t0, 2) if S.t0 else 0)
    S.log.append((event, payload))
    await sio.emit(event, payload, namespace="/demo")


# --------------------------------------------------------------------- /agent
@sio.event(namespace="/agent")
async def connect(sid, environ):
    S.agent = sid
    print("[demo] pi agent connected: %s" % sid, flush=True)
    await to_demo("wire:agent", {"up": True})
    await sio.emit("cmd:warm", {}, namespace="/agent", to=sid)


@sio.event(namespace="/agent")
async def disconnect(sid):
    if S.agent == sid:
        S.agent = None
        S.wire_ok = False
        S.reset()
    print("[demo] pi agent gone: %s" % sid, flush=True)
    await to_demo("wire:agent", {"up": False})


@sio.on("agent:hello", namespace="/agent")
async def agent_hello(sid, data):
    await to_demo("wire:hello", data)


@sio.on("ev:wire_fault", namespace="/agent")
async def ev_wire_fault(sid, data):
    """The Pi could not lock onto the board's sync stream. Surface it and keep
    Run disabled -- the alternative is discovering it mid-demo. Re-warm on a
    timer so the demo comes back by itself once the board does."""
    S.wire_ok = False
    print("[demo] wire fault: %s" % (data or {}).get("error"), flush=True)
    await to_demo("wire:fault", data)
    if S.rewarm is None or S.rewarm.done():
        S.rewarm = asyncio.create_task(_rewarm())


async def _rewarm():
    while not S.wire_ok and S.agent is not None:
        await asyncio.sleep(20)
        if S.wire_ok or S.agent is None:
            return
        print("[demo] retrying wire warm-up", flush=True)
        await sio.emit("cmd:warm", {}, namespace="/agent", to=S.agent)


@sio.on("ev:warm", namespace="/agent")
async def ev_warm(sid, data):
    S.wire_ok = True
    print("[demo] agent warm in %ss" % data.get("secs"), flush=True)
    await to_demo("wire:ready", data)


@sio.on("ev:tokenized", namespace="/agent")
async def ev_tokenized(sid, data):
    await to_demo("beat:tokenized", data)


@sio.on("ev:sent", namespace="/agent")
async def ev_sent(sid, data):
    S.rid = data.get("rid")
    # Beats 4 and 6: the certifier's fingerprints. These are the real cert
    # fields the Pi audited, not a redraw of what the Spark thinks it sent.
    await to_demo("beat:certified", data)


@sio.on("ev:opened", namespace="/agent")
async def ev_opened(sid, data):
    await to_demo("beat:answer", data)
    if not data.get("ok"):
        # Say it failed in the vocabulary every client understands. beat:answer
        # with ok=False alone left anything waiting for a verdict -- the UI, and
        # rehearse.py -- hanging politely for a run that was already over.
        await to_demo("beat:error", {
            "error": data.get("error") or "the response did not come back"})
        S.reset()
        return
    # Beat 10 follows immediately: the challenge goes back over the same wire.
    await to_demo("beat:proving", {"rid": S.rid, "mode": MODE_LABEL})
    await sio.emit("cmd:challenge", {"rid": S.rid},
                   namespace="/agent", to=S.agent)
    S.watchdog = asyncio.create_task(_proof_watchdog(S.rid))


@sio.on("ev:proof_status", namespace="/agent")
async def ev_proof_status(sid, data):
    # The prover's own status lines, arriving in band. Forwarded verbatim so the
    # progress the UI shows is the proof's, not a timer's.
    await to_demo("beat:proof_status", data)


@sio.on("ev:proof_result", namespace="/agent")
async def ev_proof_result(sid, data):
    d = dict(data or {})
    d["mode"] = MODE_LABEL
    d["model_fp"] = MODEL_FP
    # A challenge that produced no parsable RESULT line is a failure, not a
    # verdict of None -- which the UI would render as an empty panel.
    if not d.get("result"):
        if S.watchdog is not None:
            S.watchdog.cancel(); S.watchdog = None
        await to_demo("beat:error", {
            "error": "the challenge returned no result -- the prover produced no "
                     "RESULT line (check docker logs ilk_server)"})
        S.reset()
        return
    if S.watchdog is not None:
        S.watchdog.cancel()
        S.watchdog = None
    await to_demo("beat:verdict", d)
    S.running = False


@sio.on("ev:busy", namespace="/agent")
async def ev_busy(sid, data):
    await to_demo("wire:busy", data)


@sio.on("ev:error", namespace="/agent")
async def ev_error(sid, data):
    await to_demo("beat:error", data)
    S.reset()


# ---------------------------------------------------------------------- /demo
@sio.event(namespace="/demo")
async def connect(sid, environ):
    # The backlog rides INSIDE hello rather than being re-emitted as live events.
    # Replaying it on the live channel makes a client that connects after a
    # finished run see that run's beats -- including its verdict -- as if they
    # were happening now, which is indistinguishable from a fresh result and is
    # exactly the sort of thing a demo must never do.
    await sio.emit("hello", {"agent": S.agent is not None,
                             "wire": S.wire_ok,
                             "running": S.running,
                             "mode": MODE_LABEL,
                             "model": os.path.basename(MODEL_DIR),
                             "model_fp": MODEL_FP,
                             "prompt": PROMPT,
                             "backlog": [{"event": e, "data": d} for e, d in S.log]},
                   namespace="/demo", to=sid)


@sio.on("demo:run", namespace="/demo")
async def demo_run(sid, data, tampered=False):
    if S.agent is None:
        await sio.emit("beat:error", {"error": "the Pi agent is not connected"},
                       namespace="/demo", to=sid)
        return
    if not S.wire_ok:
        await sio.emit("beat:error", {
            "error": "the Pi is not locked to the board's sync stream -- the "
                     "interlock is not emitting. Power-cycle it (it goes quiet a "
                     "few hours after power-up) and the demo will re-arm itself."},
            namespace="/demo", to=sid)
        return
    if S.running:
        await sio.emit("wire:busy", {"stage": "run"}, namespace="/demo", to=sid)
        return
    S.reset()
    S.running, S.t0 = True, time.time()
    text = (data or {}).get("text") or PROMPT
    # beat:start carries whether THIS run was started tampered, so every open
    # panel banners correctly no matter who pressed the button (web switch,
    # hardware panel, rehearse) -- the client cannot know from its own switch.
    await to_demo("beat:start",
                  {"prompt": text, "mode": MODE_LABEL, "tampered": tampered})
    await sio.emit("cmd:prompt", {"text": text}, namespace="/agent", to=S.agent)


@sio.on("demo:tamper", namespace="/demo")
async def demo_tamper(sid, data=None):
    """Beat 11. Arms a one-shot flag that model_server reads at challenge time;
    the prover is then handed a response the certifier never fingerprinted.

    Deliberately NOT a weight tamper. A weight change only shows up if it moves
    an output token, and if it does the weld catches it -- but that is a longer
    story and a slower one. This failure lands in B1_out, which is proven at full
    strength in every mode, so the demo can say 'this fails every time' and mean
    it. What it does NOT demonstrate is the subsampled forward pass catching
    anything; at 1 token-layer of ~460 it usually would not."""
    open(TAMPER_FLAG, "w").close()
    await to_demo("beat:armed", {"what": "response tokens"})
    await demo_run(sid, data, tampered=True)


@sio.on("demo:reset", namespace="/demo")
async def demo_reset(sid, data=None):
    # Clear any armed tamper too: a flag left by an aborted run would otherwise
    # fire on the next honest one and look like a real failure.
    try:
        os.unlink(TAMPER_FLAG)
    except OSError:
        pass
    S.reset()
    await sio.emit("beat:reset", {}, namespace="/demo")


# The word the UI has to send back before anything halts. This is not access
# control -- anyone who can reach /demo can reach the rack -- it is there so a
# stray or replayed `demo:shutdown` cannot power the demo off by itself, which on
# a shared AP with a page open on three laptops is a real way to lose an evening.
SHUTDOWN_TOKEN = "POWER OFF"


def _halt():
    """Halt this machine, by whichever route the box actually allows.

    The Spark's `spark` account has no passwordless sudo and polkit wants an
    interactive auth, so out of the box NONE of these work and the UI is told so
    rather than being left to look like it did something. To enable it:

        echo 'spark ALL=(root) NOPASSWD: /usr/sbin/poweroff' \
          | sudo tee /etc/sudoers.d/interlock-poweroff
        sudo chmod 440 /etc/sudoers.d/interlock-poweroff

    The Pi already has blanket NOPASSWD, so its half needs nothing."""
    tried = []
    for cmd in (["systemctl", "poweroff"],
                ["sudo", "-n", "/usr/sbin/poweroff"],
                ["sudo", "-n", "shutdown", "-h", "now"]):
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        except Exception as e:
            tried.append("%s: %s" % (cmd[0], e))
            continue
        if p.returncode == 0:
            return True, " ".join(cmd)
        tried.append("%s: rc=%d %s" % (" ".join(cmd), p.returncode,
                                       (p.stderr or "").strip()[:90]))
    return False, " | ".join(tried)


@sio.on("demo:shutdown", namespace="/demo")
async def demo_shutdown(sid, data=None):
    """Halt the Pi, then halt this machine.

    Order matters and is not symmetric: the Pi's only route to a shutdown command
    is the socket it holds to this process, so this process has to still be alive
    to carry it. Send the Pi's first, give it a few seconds to land and for the Pi
    to start halting, and only then take this host -- and with it the page that
    asked -- down. There is no undo; both come back at the hardware.
    """
    if (data or {}).get("confirm") != SHUTDOWN_TOKEN:
        await sio.emit("beat:error",
                       {"error": "shutdown was not confirmed -- ignored"},
                       namespace="/demo", to=sid)
        return
    if os.environ.get("ALLOW_SHUTDOWN", "1") == "0":
        await sio.emit("beat:error",
                       {"error": "shutdown is disabled on this server "
                                 "(ALLOW_SHUTDOWN=0)"},
                       namespace="/demo", to=sid)
        return
    print("[demo] shutdown requested from %s" % sid, flush=True)
    await to_demo("beat:shutdown", {"what": "pi, then spark"})
    if S.agent is not None:
        await sio.emit("cmd:shutdown", {"confirm": SHUTDOWN_TOKEN},
                       namespace="/agent", to=S.agent)
    else:
        print("[demo] no pi agent connected -- halting this host only", flush=True)
    await asyncio.sleep(4)
    ok, how = _halt()
    print("[demo] halt %s: %s" % ("issued" if ok else "FAILED", how), flush=True)
    if not ok:
        # Still up, and the operator is entitled to know why. The Pi is already on
        # its way down, so say that too rather than implying nothing happened.
        await to_demo("beat:error", {
            "error": "the Pi was told to halt, but this host would not: %s. "
                     "Install the sudoers drop-in in demo_server._halt, or power "
                     "the Spark down at the box." % how})


# Shared with model_server through the container's /app bind mount.
TAMPER_FLAG = os.path.join(HERE, ".tamper")

# The few-shot shape is what keeps the answer terse WITHOUT cheating: with one
# worked example in front, TinyLlama's own greedy continuation is the four-word
# answer instead of a sentence plus an invented next question. Everything the
# model actually generates is still certified and proven. systemd's
# Environment= cannot carry a literal newline, so "\n" is interpreted here.
PROMPT = os.environ.get(
    "PROMPT",
    "Question: What is the capital of France?\nAnswer: Paris\n"
    "Question: What does IAEA stand for?\nAnswer:").replace("\\n", "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--host", default="0.0.0.0")
    a = ap.parse_args()
    # adapter-static in SPA mode emits ONE index.html plus /_app, and resolves every
    # route client-side. Listing routes here worked until it didn't -- /mock 404'd
    # the moment it was added -- so unknown paths fall through to the shell instead
    # and SvelteKit routes them. ASGIApp calls other_asgi_app for anything it does
    # not handle itself, which is exactly the hook for that.
    index = os.path.join(UI_DIR, "index.html")
    # NOTE "/" is deliberately NOT in static_files. ASGIApp's static server sends
    # no Cache-Control, ETag or Last-Modified, so browsers cache the shell
    # heuristically -- and the shell is the one file that must never be cached.
    # Asset filenames are content-hashed, so a stale shell asks for JS that no
    # longer exists, gets this HTML fallback back instead, and the app never
    # boots: a blank page that looks like the server is broken. Routing the shell
    # through `spa` below gives every HTML response no-store. The hashed assets
    # stay in static_files, where caching them is correct.
    static = {}
    for entry in ("_app", "robots.txt", "favicon.svg", "favicon.png"):
        path = os.path.join(UI_DIR, entry)
        if os.path.exists(path):
            static["/" + entry] = path

    async def spa(scope, receive, send):
        if scope["type"] != "http":
            return
        # The hardware button panel (docs/BUTTON-PANEL.md): plain HTTP, so the
        # Pi-side daemon needs nothing beyond the standard library and lgpio.
        path = scope.get("path", "")
        if path.startswith("/panel/"):
            if scope.get("method") == "POST" and path == "/panel/workload":
                S.workload = "tampered" if S.workload == "honest" else "honest"
                print("[panel] workload -> %s" % S.workload, flush=True)
                out = {"workload": S.workload}
            elif scope.get("method") == "POST" and path == "/panel/prompt":
                if S.workload == "tampered":
                    await demo_tamper(None, {})
                else:
                    await demo_run(None, {})
                out = {"started": S.running, "workload": S.workload}
            else:
                out = {"wire": bool(S.wire_ok and S.agent is not None),
                       "running": S.running, "workload": S.workload}
            body = json.dumps(out).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"cache-control", b"no-store")]})
            await send({"type": "http.response.body", "body": body})
            return
        # Browsers probe /favicon.ico unprompted; letting it fall through to
        # the SPA shell hands them HTML with a 200, and a browser that trusts
        # that response caches a broken icon. Point the probe at the real one.
        if path == "/favicon.ico":
            await send({"type": "http.response.start", "status": 302,
                        "headers": [(b"location", b"/favicon.svg"),
                                    (b"cache-control", b"no-store")]})
            await send({"type": "http.response.body", "body": b""})
            return
        # Read per request rather than caching at startup: `pnpm build` in the
        # dashboard would otherwise keep serving the shell this process loaded
        # hours ago, and the fix ("restart demo_server after every rebuild")
        # is exactly the kind of step that gets forgotten. It is ~1 KB.
        try:
            with open(index, "rb") as fh:
                body = fh.read()
            status = 200
        except OSError as e:
            body = ("dashboard build missing at %s (%s). Build it with "
                    "`cd dashboard && pnpm build`." % (index, e)).encode()
            status = 503
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"text/html; charset=utf-8"),
                                (b"cache-control", b"no-store, must-revalidate"),
                                (b"pragma", b"no-cache")]})
        await send({"type": "http.response.body", "body": body})

    app = socketio.ASGIApp(sio, other_asgi_app=spa, static_files=static)
    global MODEL_FP
    # A tamper flag armed for a run that never reached model_server -- the server
    # was restarted, or the proof died -- would silently fire on the next honest
    # run. That reads on stage as a genuine REJECTED with no explanation, so clear
    # it on the way up. The flag is meant to be one-shot; make startup enforce it.
    try:
        os.unlink(TAMPER_FLAG)
        print("[demo] cleared a stale tamper flag", flush=True)
    except OSError:
        pass
    print("[demo] hashing %s ..." % os.path.basename(MODEL_DIR), flush=True)
    MODEL_FP = model_fingerprint()
    print("[demo] model fingerprint: %s" % (MODEL_FP or "UNAVAILABLE"), flush=True)
    print("[demo] serving %s on %s:%d" % (UI_DIR, a.host, a.port), flush=True)
    print("[demo] mode: %s" % MODE_LABEL, flush=True)
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
