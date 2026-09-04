#!/usr/bin/env python3
"""End-to-end interlock demo driver for the Pi-client / Spark-server topology.

The one driver for the demo: prompt -> certified response -> ZK challenge -> verdict.
It replaces an earlier loopback-only driver that assumed both interlock ports were
on this box and tokenized against llama-2-7b paths that no longer exist. The live
rig is split:

    Pi (port 1 / J30, eth0)  --interlock--  Spark (port 0 / J15, enP7s7)

The Pi owns the text layer: it runs `tok.py` (the Rust `tokenizers` package and a
tokenizer.json -- no transformers, no torch), seals the ids under AES-128-GCM, and
is the only side that can open the response. This script drives that exchange and
renders progress while it happens.

Run it with the VerInf venv python:

    $VERINF/venv/bin/python -u demo_e2e.py
    $VERINF/venv/bin/python -u demo_e2e.py --prompt "Question: Name three primes.\\nAnswer:"

Stages, each timed:
  0 preflight   backend socket, ilk_server bucket lock, servo health
  1 tokenize    prompt -> canonical little-endian uint32 ids (on the Pi)
  2 send        ids over the interlock from the Pi; both certs checked
  3 decode      response ids -> text (here; the wire never carries text)
  4 challenge   in-band ZK proof, streamed live
  5 panel       the verdict block from infcli

Nothing in here changes device state: it sends one request and one challenge,
the same two things a human would type.
"""
import argparse
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import time

_APP = os.path.dirname(os.path.abspath(__file__))
# VerInf as a sibling checkout of the interlock repo; VERINF overrides.
VERINF = os.environ.get("VERINF") or os.path.join(
    os.path.dirname(os.path.dirname(_APP)), "VerInf")
# Must match the model the backend is serving: we tokenize here and send raw
# ids, so a tokenizer from a different model sends out-of-range ids and the
# backend returns an empty response. bringup.sh defaults MODEL_DIR the same
# way and pins the prover's VERINF_MODEL to it; keep all three in step.
MODEL_DIR = os.environ.get("MODEL_DIR", VERINF + "/models/TinyLlama-1.1B-Chat-v1.0")
PI = os.environ.get("PI_HOST", "2a-rpi")
PI_IFACE = os.environ.get("PI_IFACE", "eth0")
BACKEND = ("127.0.0.1", int(os.environ.get("BACKEND_PORT", "9917")))
HDR = 16  # canonical 16-byte header in front of the token payload

TTY = sys.stdout.isatty()
WIDTH = shutil.get_terminal_size((100, 24)).columns
# Erase-line is width-independent. Padding with a fixed number of spaces instead
# wraps on any terminal narrower than the pad, which leaves the cursor on the
# wrapped line and smears blanks through the transcript.
CLR = "\r\033[2K" if TTY else ""


def c(code, s):
    return "\033[%sm%s\033[0m" % (code, s) if TTY else s


BOLD, DIM, GRN, RED, YEL, CYN = "1", "2", "32", "31", "33", "36"


def stage(n, title):
    print(c(BOLD, "\n[%s] %s" % (n, title)))


def ok(msg):
    print("  " + c(GRN, "OK") + "  " + msg)


def warn(msg):
    print("  " + c(YEL, "!!") + "  " + msg)


def die(msg):
    print("  " + c(RED, "FAIL") + "  " + msg)
    sys.exit(1)


def sh(cmd, timeout=120):
    """Run locally, return (rc, combined output)."""
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def pi(cmd, timeout=120):
    """Run one command on the Pi over ssh. Quoting is the caller's problem."""
    return sh("timeout %d ssh -o BatchMode=yes %s %s" % (timeout, PI, quote(cmd)), timeout + 15)


def pi_in(cmd, data, timeout=60):
    """Same, but feed `data` on stdin — prompts contain newlines, which argv
    mangles across two shells."""
    full = "timeout %d ssh -o BatchMode=yes %s %s" % (timeout, PI, quote(cmd))
    r = subprocess.run(full, shell=True, input=data, capture_output=True,
                       text=True, timeout=timeout + 15)
    return r.returncode, (r.stdout or ""), (r.stderr or "")


TOK = "cd ~/fpe && ./venv/bin/python tok.py"


def quote(s):
    return "'" + s.replace("'", "'\\''") + "'"


def infcli(sub, timeout=120):
    """Every Pi-side invocation goes through the venv in ~/fpe and needs root for
    AF_PACKET, so keep the prefix in one place."""
    return pi("cd ~/fpe && sudo ./venv/bin/python -u infcli.py --iface %s %s" % (PI_IFACE, sub), timeout)


# --------------------------------------------------------------------------- stages

def preflight(skip):
    stage(0, "preflight")
    if skip:
        warn("skipped (--skip-preflight)")
        return
    try:
        socket.create_connection(BACKEND, 2).close()
        ok("GPU backend answering on %s:%d" % BACKEND)
    except OSError as e:
        die("backend not reachable on %s:%d (%s) -- run ./bringup.sh" % (BACKEND[0], BACKEND[1], e))

    rc, out = sh("docker logs ilk_server 2>&1")
    if rc != 0:
        die("ilk_server not running -- run ./bringup.sh")
    if "locked: bucket=" not in out:
        die("no bucket lock: the board is not emitting sync. Power-cycle the "
            "interlock (a reflash does not restart it).")
    ok("wire half locked to the board's sync stream")

    # The servo clamps at period*(SEND_FRAC-CORR_HEADROOM_FRAC) = 400us, but
    # consecutive declared-bucket shifts are 1000us apart -- so ~20% of lock
    # phases are unreachable from either shift and the loop oscillates against
    # the bound, silently dropping frames. A restart re-rolls the phase.
    recent = out.splitlines()[-40:]
    sat = sum("SATURATED" in ln for ln in recent)
    rec = sum("recover #" in ln for ln in recent)
    if sat or rec:
        warn("servo saturating (%d sat / %d recover in last 40 lines) -- placement "
             "is unreliable. Restart the wire half: ./bringup.sh" % (sat, rec))
    else:
        last = [ln for ln in recent if "servo:" in ln]
        ok("servo converged" + (" (%s)" % last[-1].split("servo:")[1].strip() if last else ""))


def tokenize(prompt):
    """Tokenize ON THE PI. The wire only ever carries canonical uint32 ids, and
    the client should be the side that turns text into them -- otherwise the
    "client" never handles the text layer at all and the server's box does it.
    The Pi runs `tokenizers` (Rust, no torch) against a 1.8 MB tokenizer.json,
    verified id-for-id against the Spark's transformers AutoTokenizer.

    Falls back to tokenizing here if the Pi's tokenizer is missing, so a Pi that
    has not been set up still runs -- but says so, because it changes who holds
    the text."""
    stage(1, "tokenize (on the Pi -- the wire never carries text)")
    rc, out, err = pi_in(TOK + " encode", prompt)
    hexs = out.strip()
    if rc != 0 or not hexs:
        warn("Pi tokenizer unavailable (%s) -- falling back to tokenizing HERE"
             % (err.strip().splitlines()[-1] if err.strip() else "rc=%d" % rc))
        from transformers import AutoTokenizer
        tk = AutoTokenizer.from_pretrained(MODEL_DIR)
        ids = tk(prompt, add_special_tokens=True)["input_ids"]
        hexs = b"".join(struct.pack("<I", i) for i in ids).hex()
    else:
        ids = [struct.unpack_from("<I", bytes.fromhex(hexs), i)[0]
               for i in range(0, len(hexs) // 2, 4)]
    ok("%d tokens -> %d B canonical payload" % (len(ids), len(ids) * 4))
    print(c(DIM, "      %s" % prompt.replace("\n", "\\n")))
    return None, ids, hexs


def send(hexs, wait_ms, ids=None):
    stage(2, "send over the interlock (Pi -> Spark, AES-128-GCM)")
    # `send --ids` seals on the Pi: the payload the interlock certifies is the
    # crypto header (nonce, KEY_COMMIT, GCM tag) plus ciphertext. No token
    # crosses the cable in clear, and the key is derived from the PSK on each
    # end rather than transmitted. `--hex` remains the cleartext bring-up path.
    if ids is not None:
        arg = "--ids " + ",".join(str(int(v)) for v in ids)
    else:
        arg = "--hex " + hexs
    rc, out = infcli("--wait-ms %d send %s" % (wait_ms, arg), timeout=180)
    m = re.search(r"sent request rid=(\d+)", out)
    if not m:
        die("send produced no rid:\n" + out.strip())
    rid = int(m.group(1))
    # Match infcli's own severity: only False is a failure. `n/a` means the cert's
    # period started before this client's capture window opened, so it declines to
    # audit a set it knows is partial (infcli.py:301) -- weaker, but not wrong, and
    # it does not gate the challenge.
    for ln in out.splitlines():
        if "cert:" in ln:
            if "tau=FAIL" in ln or "byte-audit=FAIL" in ln:
                mark = c(RED, "FAIL")
            elif "byte-audit=n/a" in ln:
                mark = c(YEL, "n/a ")
            else:
                mark = c(GRN, "OK  ")
            print("  " + mark + "  " + ln.strip())
    kc = re.search(r'"key_commit":\s*"([0-9a-f]{64})"', out)
    ok("rid=%d%s" % (rid, ("  KEY_COMMIT=%s..." % kc.group(1)[:16]) if kc else ""))
    return rid


def decode(_tok, rid):
    stage(3, "decode response ids -> text (on the Pi)")
    rc, out = infcli("show %d" % rid, timeout=120)
    m = re.search(r'"response_data":\s*"([0-9a-f]*)"', out)
    if not m or not m.group(1):
        die("no response_data for rid=%d" % rid)
    # Encrypted runs record the decrypted ids alongside the raw payload: the Pi
    # holds the per-request key, so it is the side that can open the response.
    mi = re.search(r'"response_ids":\s*\[([0-9,\s]*)\]', out)
    cerr = re.search(r'"crypto_error":\s*"([^"]+)"', out)
    if cerr:
        die("response failed authentication on the Pi: %s\n"
            "The GCM tag is checked before any id is used, so this is a forged, "
            "corrupted, or wrongly-keyed payload -- not a decode problem." % cerr.group(1))
    if mi:
        ids = [int(v) for v in mi.group(1).split(",") if v.strip()]
        hexids = "".join("%08x" % 0 for _ in ())          # unused on this path
    else:
        payload = bytes.fromhex(m.group(1))[HDR:]
        ids = [struct.unpack_from("<I", payload, i)[0] for i in range(0, len(payload), 4)]
    # An empty response almost always means the tokenizer here disagrees with the
    # model the backend loaded (ids outside its vocab). Say so plainly rather than
    # letting it surface later as a MISMATCH in the binding panel.
    if not ids:
        die("empty response for rid=%d -- backend returned no tokens. Most likely "
            "MODEL_DIR here (%s) is not the model the backend is serving; check "
            "the backend log line 'listening on ... (model=...)'." % (rid, MODEL_DIR))
    # Detokenize on the Pi too, for the same reason as stage 1: the client owns
    # text. Falls back here if the Pi's tokenizer is missing.
    id_hex = "".join(struct.pack("<I", int(v) & 0xFFFFFFFF).hex() for v in ids)
    rc2, out2 = pi(TOK + " decode " + id_hex, timeout=60)
    if rc2 == 0 and out2.strip():
        text = out2
    else:
        from transformers import AutoTokenizer
        tk = AutoTokenizer.from_pretrained(MODEL_DIR)
        text = tk.decode(ids, clean_up_tokenization_spaces=False)
    ok("%d response tokens" % len(ids))
    print(c(CYN, "      " + text.strip().replace("\n", "\n      ")))
    return text


def challenge(rid, timeout_s):
    """Stream the in-band challenge and render its progress.

    infcli already prints `[status]` lines as the prover reports; the sweep line
    restarts at 0 for each sweep, so a single monotonic bar would lie. Track
    sweeps by detecting the reset and show which one is in flight.
    """
    stage(4, "in-band ZK challenge (proof runs and is verified on the Spark)")
    cmd = ("timeout %d ssh -o BatchMode=yes %s %s"
           % (timeout_s + 60, PI,
              quote("cd ~/fpe && sudo ./venv/bin/python -u infcli.py --iface %s "
                    "--challenge-timeout %d challenge %d" % (PI_IFACE, timeout_s, rid))))
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, bufsize=1)
    t0 = time.time()
    sweep, last_pct, panel = 0, 101, []
    for raw in p.stdout:
        ln = raw.rstrip("\n")
        if panel or ln.startswith("=" * 10) or "VERIFIED CONVERSATION TURN" in ln:
            panel.append(ln)
            continue
        m = re.search(r"\[sweep\] op (\d+)/(\d+) \((\d+)%\).*?eta=([\d.]+m)", ln)
        if m:
            cur, tot, pct, eta = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
            if pct < last_pct:
                # A reset means the previous sweep finished. The live bar is
                # transient, so leave one permanent line per sweep behind it --
                # otherwise the scrollback shows no trace that any work happened.
                if sweep:
                    sys.stdout.write(CLR)
                    print("  " + c(DIM, "sweep %d complete  (%.0fs)" % (sweep, time.time() - t0)))
                sweep += 1
            last_pct = pct
            bar = "#" * (pct * 30 // 100)
            line = ("  sweep %-2d [%-30s] %3d%% (%d/%d) eta %-5s  %5.0fs elapsed"
                    % (sweep, bar, pct, cur, tot, eta, time.time() - t0))
            if TTY:
                sys.stdout.write(CLR + line[:WIDTH - 1])
                sys.stdout.flush()
            else:
                print(line)
            continue
        note = ln.strip()
        if note.startswith("[status]"):
            note = note[len("[status]"):].strip()
        # The Pi's own canon_tx servo chatter is liveness noise, not challenge
        # progress -- preflight already reports servo health, so keep it out of
        # the demo display.
        if note.startswith("# [canon_tx]"):
            continue
        if note:
            sys.stdout.write(CLR)
            print("  " + c(DIM, note[:WIDTH - 3]))
    p.wait()
    sys.stdout.write(CLR)
    ok("challenge finished in %.0f s" % (time.time() - t0))
    return panel, p.returncode


def main():
    ap = argparse.ArgumentParser(description="End-to-end interlock demo (Pi client -> Spark server).")
    ap.add_argument("--prompt", default="Question: What is the capital of France?\nAnswer:")
    ap.add_argument("--wait-ms", type=int, default=30000)
    ap.add_argument("--challenge-timeout", type=int, default=2400)
    ap.add_argument("--skip-preflight", action="store_true")
    ap.add_argument("--no-challenge", action="store_true",
                    help="stop after the certified round trip (seconds, no proof)")
    a = ap.parse_args()

    t0 = time.time()
    preflight(a.skip_preflight)
    tok, _ids, hexs = tokenize(a.prompt.replace("\\n", "\n"))
    rid = send(hexs, a.wait_ms, ids=_ids)
    decode(tok, rid)
    if a.no_challenge:
        print(c(BOLD, "\ndone in %.0f s (no challenge; --no-challenge)" % (time.time() - t0)))
        return
    panel, rc = challenge(rid, a.challenge_timeout)
    stage(5, "verdict")
    # Keep infcli's own indentation: it pairs H(proof) under H(local) for each
    # direction, which is the whole point of the INPUT MATCH block.
    for ln in panel:
        if "RESULT:" in ln:
            print("  " + c(GRN if "PASS" in ln else RED, ln))
        else:
            print("  " + ln)
    print(c(BOLD, "\ntotal %.0f s" % (time.time() - t0)))
    sys.exit(0 if any("RESULT: PASS" in l for l in panel) else 1)


if __name__ == "__main__":
    main()
