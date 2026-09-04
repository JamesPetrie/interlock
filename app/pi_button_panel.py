#!/usr/bin/env python3
"""Hardware button panel for the interlock demo. Wiring: docs/BUTTON-PANEL.md.

Two momentary buttons and two LEDs on the Pi's header, speaking plain HTTP to
the demo orchestrator on the Spark. Standard library plus lgpio (shipped with
Raspberry Pi OS) -- nothing to install, nothing that needs the internet.

  PROMPT button    -> POST /panel/prompt    starts a run of the current workload
  WORKLOAD button  -> POST /panel/workload  toggles honest <-> tampered

LED language:
  green PROMPT LED    always lit -- it marks the button, not the wire
  bicolor WORK LED    green = honest workload
                      red   = tampered workload

Safe to run unwired: the button lines idle high on internal pull-ups, so an
empty header reads as "nothing pressed" and the daemon just polls state.
"""
import json
import threading
import time
import urllib.request

import lgpio

SPARK = "http://10.42.0.1:80"

# BCM numbers -- see docs/BUTTON-PANEL.md for the physical pins
BTN_PROMPT = 17     # pin 11, to GND: start a run
LED_PROMPT = 27     # pin 13, green LED: ready / running
BTN_WORK = 22       # pin 15, to GND: toggle the workload
LED_WORK_G = 23     # pin 16, bicolor green leg
LED_WORK_R = 24     # pin 18, bicolor red leg

POLL_S = 0.02       # button scan
STATE_S = 0.7       # orchestrator state refresh
DEBOUNCE_TICKS = 3  # ~60 ms


def http(method, path, timeout=5):
    try:
        req = urllib.request.Request(SPARK + path, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        print("[panel] %s %s failed: %s" % (method, path, e), flush=True)
        return None


def open_chip():
    """The header GPIOs moved between gpiochip4 and gpiochip0 across Pi 5
    kernels; take the first chip that lets us claim our lines."""
    last = None
    for n in (0, 4, 1, 2, 3):
        try:
            h = lgpio.gpiochip_open(n)
        except lgpio.error as e:
            last = e
            continue
        try:
            lgpio.gpio_claim_input(h, BTN_PROMPT, lgpio.SET_PULL_UP)
            lgpio.gpio_claim_input(h, BTN_WORK, lgpio.SET_PULL_UP)
            lgpio.gpio_claim_output(h, LED_PROMPT, 0)
            lgpio.gpio_claim_output(h, LED_WORK_G, 0)
            lgpio.gpio_claim_output(h, LED_WORK_R, 0)
            print("[panel] gpiochip%d" % n, flush=True)
            return h
        except lgpio.error as e:
            last = e
            lgpio.gpiochip_close(h)
    raise SystemExit("no usable gpiochip: %s" % last)


class Panel:
    def __init__(self):
        self.h = open_chip()
        self.wire = False
        self.running = False
        self.workload = "honest"
        self.lock = threading.Lock()

    # ── LEDs ────────────────────────────────────────────────────────────────
    def leds(self):
        with self.lock:
            workload = self.workload
        # steady states only, no blinking. The green LED is a lamp on the
        # button itself -- always lit, so the panel is findable in a dark
        # room regardless of what the wire is doing.
        lgpio.gpio_write(self.h, LED_PROMPT, 1)
        lgpio.gpio_write(self.h, LED_WORK_G, 1 if workload == "honest" else 0)
        lgpio.gpio_write(self.h, LED_WORK_R, 0 if workload == "honest" else 1)

    # ── orchestrator state ──────────────────────────────────────────────────
    def poll_state(self):
        while True:
            st = http("GET", "/panel/state")
            if st is not None:
                with self.lock:
                    self.wire = bool(st.get("wire"))
                    self.running = bool(st.get("running"))
                    self.workload = st.get("workload", "honest")
            time.sleep(STATE_S)

    # ── button actions (threads, so the scan loop never blocks on HTTP) ─────
    def press_prompt(self):
        print("[panel] PROMPT pressed", flush=True)
        st = http("POST", "/panel/prompt")
        if st is not None:
            with self.lock:
                self.running = bool(st.get("started"))

    def press_work(self):
        print("[panel] WORKLOAD pressed", flush=True)
        st = http("POST", "/panel/workload")
        if st is not None:
            with self.lock:
                self.workload = st.get("workload", self.workload)

    # ── main scan loop ──────────────────────────────────────────────────────
    def run(self):
        threading.Thread(target=self.poll_state, daemon=True).start()
        down = {BTN_PROMPT: 0, BTN_WORK: 0}
        fired = {BTN_PROMPT: False, BTN_WORK: False}
        act = {BTN_PROMPT: self.press_prompt, BTN_WORK: self.press_work}
        while True:
            for pin in (BTN_PROMPT, BTN_WORK):
                if lgpio.gpio_read(self.h, pin) == 0:      # pressed (to GND)
                    down[pin] += 1
                    if down[pin] >= DEBOUNCE_TICKS and not fired[pin]:
                        fired[pin] = True
                        threading.Thread(target=act[pin], daemon=True).start()
                else:
                    down[pin] = 0
                    fired[pin] = False
            self.leds()
            time.sleep(POLL_S)


if __name__ == "__main__":
    print("[panel] starting; orchestrator at %s" % SPARK, flush=True)
    Panel().run()
