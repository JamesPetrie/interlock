#!/usr/bin/env python3
"""Headless /demo client: drive one run and print every beat. Verifies the whole
chain without a browser."""
import sys, time, socketio
sio = socketio.Client()
done = {"v": False}

started = {"v": False}

@sio.on("*", namespace="/demo")
def any_ev(event, data):
    if event == "hello":
        print("  hello agent=%s running=%s backlog=%d"
              % (data.get("agent"), data.get("running"),
                 len(data.get("backlog") or [])), flush=True)
        return
    print("  %-22s %s" % (event, data), flush=True)
    if event == "beat:start":
        started["v"] = True
    if started["v"] and event in ("beat:verdict", "beat:error"):
        done["v"] = True

@sio.event(namespace="/demo")
def connect():
    print("[probe] connected", flush=True)

sio.connect(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8770",
            namespaces=["/demo"], transports=["websocket"])
time.sleep(1)
print("[probe] demo:run", flush=True)
sio.emit("demo:reset", {}, namespace="/demo")
time.sleep(0.3)
sio.emit("demo:run", {}, namespace="/demo")
t0 = time.time()
while not done["v"] and time.time() - t0 < 180:
    time.sleep(0.5)
print("[probe] finished in %.1fs" % (time.time() - t0), flush=True)
sio.disconnect()
