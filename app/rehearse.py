#!/usr/bin/env python3
"""Rehearse the web demo end to end, and fail loudly if it would embarrass you.

postboot-check.sh proves the services are up; it cannot prove a run PASSES. The
day this was written, every check on the box was green and every honest run
said FAIL -- the sound path was invoking the Rust verifier without its policy
arguments, and nothing noticed until a browser watched a proof fail. This
script is that browser: it drives the same /demo socket the dashboard uses,
runs one honest prompt and one tampered one, and demands PASS then FAIL.

Costs two real proofs (~7 min as deployed). Run it after touching anything on
the proof path, before an audience does.

    PYTHONPATH=/usr/lib/python3/dist-packages \
        /home/spark/v2/VerInf/venv/bin/python rehearse.py [url]

The PYTHONPATH splice: the venv has socketio but not requests, the system
python has requests but not socketio, and teaching either environment the
other's package means touching the validated venv. Splicing is smaller.
"""
import json
import sys
import threading
import time

import socketio

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:80"
# Well past the deployed sound run (~200 s); the server's own watchdog fires at
# 420 s and its beat:error ends the wait early anyway.
VERDICT_TIMEOUT = 480

sio = socketio.Client()
got = {}                       # the terminal event of the current phase
done = threading.Event()


@sio.on("*", namespace="/demo")
def _any(ev, data=None):
    d = data or {}
    if ev == "beat:verdict":
        got.update(event=ev, result=d.get("result") or {}, mode=d.get("mode"))
        done.set()
    elif ev == "beat:error":
        got.update(event=ev, error=d.get("error"))
        done.set()
    elif ev == "wire:busy":
        got.update(event=ev, error="another run is already in flight")
        done.set()


def phase(name, cmd, want):
    """Fire one run and compare the verdict fields against `want`."""
    got.clear()
    done.clear()
    sio.emit("demo:reset", {}, namespace="/demo")
    time.sleep(1)
    t0 = time.time()
    print("[rehearse] %s: started (waiting up to %ds)" % (name, VERDICT_TIMEOUT),
          flush=True)
    sio.emit(cmd, {}, namespace="/demo")
    if not done.wait(VERDICT_TIMEOUT):
        print("[rehearse] %s: NO VERDICT after %ds" % (name, VERDICT_TIMEOUT))
        return False
    if got["event"] != "beat:verdict":
        print("[rehearse] %s: %s -- %s" % (name, got["event"], got.get("error")))
        return False
    r = got["result"]
    bad = {k: (r.get(k), v) for k, v in want.items() if r.get(k) != v}
    line = "verdict=%(verdict)s verify=%(verify)s keybind=%(keybind)s U=%(U)s" % {
        k: r.get(k) for k in ("verdict", "verify", "keybind", "U")}
    if bad:
        print("[rehearse] %s: WRONG after %.0fs -- %s; expected %s"
              % (name, time.time() - t0, line,
                 " ".join("%s=%s" % (k, v) for k, (_, v) in bad.items())))
        return False
    print("[rehearse] %s: as it should be after %.0fs -- %s"
          % (name, time.time() - t0, line), flush=True)
    return True


def main():
    sio.connect(URL, namespaces=["/demo"], wait_timeout=10)
    time.sleep(1)
    # An honest run must PASS, and a tampered one must FAIL on the key binding
    # -- that pair is the demo. Either alone proves much less: a rig that FAILs
    # everything also "catches" the tamper.
    ok = phase("honest run", "demo:run", {"verdict": "PASS", "verify": "ACCEPT",
                                          "keybind": "OK"})
    ok &= phase("tampered run", "demo:tamper", {"verdict": "FAIL",
                                                "verify": "REJECT",
                                                "keybind": "FAIL"})
    sio.emit("demo:reset", {}, namespace="/demo")   # leave nothing armed
    time.sleep(1)
    sio.disconnect()
    print("[rehearse] %s" % ("READY -- both verdicts landed where they must"
                             if ok else "NOT READY -- fix it before showing it"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
