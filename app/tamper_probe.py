#!/usr/bin/env python3
"""Drive one TAMPERED run and print the verdict. Beat 11 must fail, and must fail
as a verdict rather than a traceback."""
import sys, time, socketio
sio = socketio.Client(); done = {"v": False}; started = {"v": False}

@sio.on("*", namespace="/demo")
def any_ev(event, data):
    if event == "hello": return
    print("  %-20s %s" % (event, str(data)[:190]), flush=True)
    if event == "beat:start": started["v"] = True
    if started["v"] and event in ("beat:verdict", "beat:error"): done["v"] = True

sio.connect("http://127.0.0.1:8770", namespaces=["/demo"], transports=["websocket"])
time.sleep(1)
sio.emit("demo:reset", {}, namespace="/demo"); time.sleep(0.5)
print("[probe] demo:tamper", flush=True)
sio.emit("demo:tamper", {}, namespace="/demo")
t0 = time.time()
while not done["v"] and time.time() - t0 < 240: time.sleep(0.5)
print("[probe] finished in %.1fs" % (time.time() - t0), flush=True)
sio.disconnect()
