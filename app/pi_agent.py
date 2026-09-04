#!/usr/bin/env python3
"""The Pi half of the web demo: a resident client that owns the wire.

WHY THIS EXISTS. demo_e2e.py drives the Pi over SSH -- four `ssh 2a-rpi 'sudo
infcli.py ...'` invocations per run. That is wrong three ways. Each invocation is
a fresh process, so the canonical port is reopened and the 24-probe declared-bucket
bootstrap re-runs every stage. Results come back by regexing JSON out of `infcli
show` on stdout. And it inverts the picture the demo is drawing: the datacenter
reaches into the user's machine as root.

This process instead stays up, holds the port for its whole life (infcli caches it
in `_PORT`, so importing and calling repeatedly is enough), and speaks Socket.IO to
the Spark's orchestrator.

WHAT CROSSES WHICH LINK. The control link carries commands and metadata only --
never a prompt, never a response, never key material. The prompt and the response
cross the certified wire, sealed, and nothing else does. That is what lets the demo
answer "is this staged?" by pointing at the cable: you can tcpdump the control link
and find only JSON envelopes. `_scrub` enforces it on the way out rather than
leaving it to each emit site to remember.

THE PSK NEVER MOVES. It is provisioned out of band at ~/.interlock/psk (root, since
this runs under sudo for AF_PACKET). sync_pi.sh deliberately does not sync it.

Run:  sudo ./venv/bin/python -u pi_agent.py --iface eth0 --spark http://2a-spark:80
"""
import argparse
import os
import subprocess
import sys
import threading
import time
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import socketio

import infcli
import tok

# The orchestrator serves /agent and /demo; this process is the sole /agent.
NS = "/agent"

# Metadata we are willing to put on the control link. Everything else is dropped:
# an allow-list fails closed when someone adds a field to infcli's log entry.
SAFE_FIELDS = {
    "rid", "nonce", "key_commit", "ct_in", "ct_out", "encrypted",
    "request_audit", "response_audit", "bucket_start", "num_buckets",
    "tau_req_ok", "tau_rsp_ok", "crypto_error",
}


def _scrub(entry):
    """Keep only the fields the UI needs. Ciphertext hex is fine -- it is on the
    cable already -- but ids, text and anything key-shaped are not."""
    out = {k: entry.get(k) for k in SAFE_FIELDS if k in entry}
    for side, field in (("request", "overall_req"), ("response", "overall_rsp")):
        cert = entry.get("%s_cert" % side)
        if not cert:
            continue
        out["%s_cert_len" % side] = len(cert) // 2
        # The digest the CERTIFIER computed -- the epoch root it signed over that
        # period's traffic. The UI labels these "fingerprint", so they have to be
        # the certifier's own values, not a stand-in like KEY_COMMIT that happens
        # to be nearby in the same packet.
        try:
            out["%s_digest" % side] = infcli.parse_cert(infcli.ub(cert))[field].hex()
        except Exception:
            pass
    return out


class Agent:
    def __init__(self, args):
        self.a = self._namespace(args)
        self.sio = socketio.Client(reconnection=True, reconnection_delay=1,
                                   reconnection_delay_max=10)
        # One request in flight, globally. commit.bucket_hash raises on a bucket
        # holding more than one packet -- the combining rule was never pinned
        # against silicon -- and the app only escapes that by keeping a single
        # packet on the wire so each lands alone in its 1 ms bucket. Two
        # concurrent prompts would not race a little, they would throw.
        self.lock = threading.Lock()
        # Warming must be serialised. infcli.canon_port() caches the port, but the
        # check is not atomic: two concurrent warms both see an empty cache, both
        # open a port on the same wire, and the interlock -- which tolerates exactly
        # one packet in flight -- rejects the probes of both. The loser then nulls
        # the cache on its way out and takes the good port with it. Serialising
        # makes the second caller find the first one's port and simply re-report it.
        self.warm_lock = threading.Lock()
        # (name, monotonic deadline) while a wire job holds the lock; None when
        # idle. The watchdog reads it, the job threads write it.
        self.job = None
        threading.Thread(target=self._watch, daemon=True).start()
        self._wire(args)

    # ── the job watchdog ───────────────────────────────────────────────────
    # A prompt or challenge thread blocked on a dead wire (a board or Spark
    # rebooted mid-operation) can hold the single-flight lock far longer than
    # any demo can wait -- observed 2026-08-27, when every PROMPT after a Spark
    # reboot bounced off a lock a stranded thread still held, until a human
    # restarted this agent. A thread stuck in a native wait cannot be killed
    # from Python, so the watchdog does the one clean thing available: declare
    # the fault and exit. systemd (Restart=always) brings the agent back in
    # seconds with a fresh port and a clean lock -- which is exactly what the
    # human restart did, minus the human.
    PROMPT_DEADLINE_S = 120.0
    # Warm bootstraps the port, and a bootstrap ridden through a link flap has
    # been seen to take ~2 minutes legitimately -- but also to hang forever
    # when the board's Pi-facing port dropped carrier mid-warm (observed
    # 2026-08-27: warm stranded 13 minutes holding the lock, every later run
    # refused, until a human restarted the agent). Generous, then covered.
    WARM_DEADLINE_S = 300.0

    def _watch(self):
        while True:
            time.sleep(5)
            job = self.job
            if job is None or time.monotonic() < job[1]:
                continue
            print("[agent] WATCHDOG: %s hung past its deadline -- exiting so "
                  "systemd restarts the agent clean" % job[0], flush=True)
            try:
                self.emit("ev:wire_fault",
                          {"error": "the agent's %s hung past its deadline; the "
                                    "agent is restarting itself" % job[0]})
                time.sleep(1)              # let the emit flush before dying
            except Exception:
                pass
            os._exit(70)

    @staticmethod
    def _namespace(args):
        """infcli's functions take the argparse namespace `main` builds. Rebuild it
        here with the same defaults rather than shelling out to infcli."""
        return types.SimpleNamespace(
            iface=args.iface, model=args.model,
            gap_ms=300, wait_ms=15000,
            key=infcli.DEFAULT_KEY,
            challenge_timeout=args.challenge_timeout,
            system="", user_tag="Question:", bot_tag="Answer:")

    # ------------------------------------------------------------------ wiring
    def _wire(self, args):
        sio = self.sio

        @sio.event(namespace=NS)
        def connect():
            print("[agent] connected to %s" % args.spark, flush=True)
            sio.emit("agent:hello", {"iface": args.iface}, namespace=NS)
            # Re-assert the wire's state on every (re)connect, because emitting it
            # once is not enough. The port is warmed as soon as the agent starts,
            # which can finish BEFORE the socket is up -- then ev:warm goes into a
            # namespace that is not connected yet ("emit ev:warm failed: /agent is
            # not a connected namespace"), is dropped, and the server shows a
            # perfectly healthy wire as faulted with Run disabled until someone
            # restarts the agent. Observed after a Spark reboot, which is exactly
            # when the server is slowest to come up. Warming again is cheap (both
            # the port and the tokenizer are cached) and genuinely retries when the
            # first attempt failed, so this doubles as the recovery path.
            threading.Thread(target=_warm, daemon=True).start()

        @sio.event(namespace=NS)
        def disconnect():
            print("[agent] disconnected", flush=True)

        @sio.on("cmd:warm", namespace=NS)
        def _warm(_data=None):
            """Open the port and load the tokenizer before anyone is watching.
            Both are slow exactly once; doing it on the first prompt would put
            the flywheel lock and a tokenizer load inside beat 4.

            This used to let the exception escape. When the board goes quiet --
            which it does a few hours after power-up -- canon_port raises "no sync
            stream", the exception died in this handler thread, and the UI saw the
            agent connected with no fault: Run stayed enabled and failed twelve
            seconds later, in front of the audience. Report it instead, so the
            wire shows as faulted before anyone can press anything."""
            t0 = time.time()
            # the watchdog covers warm too: a warm stranded in a native wait
            # holds the single-flight lock forever, and the only clean exit is
            # the same one a hung prompt gets -- die, restart, rebind
            self.job = ("warm", time.monotonic() + self.WARM_DEADLINE_S)
            try:
                with self.warm_lock:
                    p = infcli.canon_port(self.a)
                    # A cached port can outlive the board. A power-cycled
                    # interlock comes back on a fresh sync epoch, the old
                    # bootstrap is void, and send() would raise "send() before
                    # bootstrap()" at prompt time -- with the wire still showing
                    # healthy, because this warm was happily returning the stale
                    # cache. Observed after a mid-session FPGA restart. recover()
                    # relocks and re-bootstraps the same port in place.
                    if p.decl_shift is None:
                        print("[agent] warm: cached port lost its bootstrap -- "
                              "recovering", flush=True)
                        p.recover()
                    tok.tokenizer()
            except Exception as e:
                # Drop the cached half-open port so the next warm genuinely retries
                # rather than handing back a dead object.
                infcli._PORT["p"] = None
                msg = "%s: %s" % (type(e).__name__, e)
                print("[agent] warm FAILED: %s" % msg, flush=True)
                self.emit("ev:wire_fault", {"error": msg})
                return
            finally:
                self.job = None
            self.emit("ev:warm", {"secs": round(time.time() - t0, 1)})

        @sio.on("cmd:prompt", namespace=NS)
        def _prompt(data):
            threading.Thread(target=self._do_prompt, args=(data,),
                             daemon=True).start()

        @sio.on("cmd:challenge", namespace=NS)
        def _challenge(data):
            threading.Thread(target=self._do_challenge, args=(data,),
                             daemon=True).start()

        @sio.on("cmd:shutdown", namespace=NS)
        def _shutdown(data=None):
            """Halt the Pi, on the Spark's say-so.

            The Spark is about to halt itself, and this socket is the Pi's only
            route to a command from the front panel, so the two cannot be issued
            in parallel -- the Spark sends this first and then waits. Do the halt
            off the socket thread so the ack gets out before init starts tearing
            the process down.

            This already runs as root (AF_PACKET), so `shutdown` needs no sudo;
            the sudo attempt is only there for a non-root run during development."""
            if (data or {}).get("confirm") != "POWER OFF":
                print("[agent] unconfirmed shutdown ignored", flush=True)
                return
            print("[agent] shutdown requested by the Spark", flush=True)

            def _halt():
                time.sleep(1)
                for cmd in (["shutdown", "-h", "now"],
                            ["sudo", "-n", "shutdown", "-h", "now"]):
                    try:
                        p = subprocess.run(cmd, capture_output=True, text=True,
                                           timeout=15)
                    except Exception as e:
                        print("[agent] %s: %s" % (cmd[0], e), flush=True)
                        continue
                    if p.returncode == 0:
                        return
                    print("[agent] %s rc=%d %s" % (" ".join(cmd), p.returncode,
                                                   (p.stderr or "").strip()),
                          flush=True)
                print("[agent] could not halt the Pi", flush=True)

            threading.Thread(target=_halt, daemon=True).start()

    def emit(self, ev, payload):
        try:
            self.sio.emit(ev, payload, namespace=NS)
        except Exception as e:                      # a dead UI must not kill the wire
            print("[agent] emit %s failed: %s" % (ev, e), flush=True)

    # ------------------------------------------------------------------ actions
    def _do_prompt(self, data):
        text = data.get("text") or ""
        if not self.lock.acquire(blocking=False):
            self.emit("ev:busy", {"stage": "prompt"})
            return
        self.job = ("prompt", time.monotonic() + self.PROMPT_DEADLINE_S)
        try:
            t0 = time.time()
            ids = tok.encode_ids(text)
            # The token COUNT is metadata; the ids are not -- they are the prompt.
            self.emit("ev:tokenized", {"n_tokens": len(ids),
                                       "payload_bytes": 4 * len(ids),
                                       "secs": round(time.time() - t0, 2)})

            t1 = time.time()
            entry = infcli.send_tokens(self.a, ids)
            info = _scrub(entry)
            info["secs"] = round(time.time() - t1, 2)
            self.emit("ev:sent", info)

            rsp_ids = entry.get("response_ids")
            if entry.get("crypto_error"):
                self.emit("ev:opened", {"rid": entry["rid"], "ok": False,
                                        "error": entry["crypto_error"]})
                return
            # No response at all is a FAILURE, not an empty success. Reporting
            # ok=True with zero tokens paints a blank answer and then a verdict
            # built from nothing. The usual cause is being on the wrong port --
            # the client half only receives on the certified client link -- so
            # say that rather than showing an empty box.
            if not rsp_ids:
                self.emit("ev:opened", {
                    "rid": entry["rid"], "ok": False,
                    "error": "no response returned. Certificates: request=%s "
                             "response=%s. If both are missing, this host is "
                             "probably on the compute port -- the client belongs "
                             "on J30." % (entry.get("request_audit"),
                                          entry.get("response_audit"))})
                return
            # The decrypted text is the demo's output and the one plaintext the UI
            # is entitled to. It is emitted from HERE, by the holder of the key,
            # which is the point being made -- the Spark cannot show it on the
            # Pi's behalf.
            out = tok.decode_ids(rsp_ids) if rsp_ids else ""
            self.emit("ev:opened", {"rid": entry["rid"], "ok": True,
                                    "n_tokens": len(rsp_ids or []), "text": out})
        except Exception as e:
            msg = "%s: %s" % (type(e).__name__, e)
            self.emit("ev:error", {"stage": "prompt", "error": msg})
            print("[agent] prompt failed: %s" % msg, flush=True)
            self._maybe_wire_fault(msg)
        finally:
            self.job = None
            self.lock.release()

    def _do_challenge(self, data):
        rid = int(data.get("rid", 0))
        if not self.lock.acquire(blocking=False):
            self.emit("ev:busy", {"stage": "challenge"})
            return
        # The challenge's own timeout is the ceiling a caller chose; the
        # watchdog only catches what outlives even that.
        self.job = ("challenge",
                    time.monotonic() + self.a.challenge_timeout + 60)
        try:
            t0 = time.time()
            # do_challenge streams the prover's own status lines as they arrive in
            # band. Forwarding them straight through is what makes the UI's proof
            # progress real rather than a timer pretending to be one.
            res = infcli.do_challenge(
                self.a, rid,
                lambda s: self.emit("ev:proof_status", {"rid": rid, "line": s}))
            self.emit("ev:proof_result", {"rid": rid, "result": res,
                                          "secs": round(time.time() - t0, 1)})
        except Exception as e:
            msg = "%s: %s" % (type(e).__name__, e)
            self.emit("ev:error", {"stage": "challenge", "error": msg})
            print("[agent] challenge failed: %s" % msg, flush=True)
            self._maybe_wire_fault(msg)
        finally:
            self.job = None
            self.lock.release()

    # Every string the wire layer raises when the PORT is the problem, as opposed
    # to the request: a voided bootstrap (board power-cycled mid-session), a dead
    # sync stream, or send_confirmed exhausting its attempts against latched
    # placement. The first cut matched only the first two, so an exhausted
    # send_confirmed left the wire showing healthy while every prompt burned its
    # full retry budget and failed -- one bad run becoming a bad afternoon.
    WIRE_FAULT_MARKS = ("bootstrap", "no sync", "send_confirmed", "placement",
                        "flywheel")

    def _maybe_wire_fault(self, msg):
        """Drop the cached port and declare a fault when the failure was the
        wire's, so the server disables Run and the re-warm loop takes over."""
        low = msg.lower()
        if any(m in low for m in self.WIRE_FAULT_MARKS):
            infcli._PORT["p"] = None
            self.emit("ev:wire_fault", {"error": msg})

    def run(self, url):
        while True:
            try:
                self.sio.connect(url, namespaces=[NS], transports=["websocket"])
                self.sio.wait()
            except Exception as e:
                print("[agent] connect failed (%s); retrying in 3s" % e, flush=True)
                time.sleep(3)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--iface", default="eth0")
    # Port 80: the orchestrator runs as a boot service (interlock-demo.service).
    # demo_up.sh passes SPARK_URL explicitly and wins over this either way.
    ap.add_argument("--spark", default=os.environ.get("SPARK_URL",
                                                      "http://2a-spark:80"))
    # Only infcli's own transformers path uses --model, which this agent never
    # takes; tok.py finds its tokenizer at app/tokenizer/tinyllama (TOKENIZER_DIR
    # overrides). Kept so infcli's namespace is complete.
    ap.add_argument("--model", default="")
    # infcli's own default is 1800 s, sized for a full-soundness proof on a big
    # model. Here fast mode returns in ~15 s and sound in ~110 s, so 1800 just
    # means a dead wire hangs the demo for half an hour. demo_server's watchdog
    # gives up before this does; this is the backstop under it.
    ap.add_argument("--challenge-timeout", type=int, default=600)
    a = ap.parse_args()
    if os.geteuid() != 0:
        sys.exit("pi_agent needs root for AF_PACKET on %s" % a.iface)
    print("[agent] starting; wire=%s spark=%s" % (a.iface, a.spark), flush=True)
    Agent(a).run(a.spark)


if __name__ == "__main__":
    main()
