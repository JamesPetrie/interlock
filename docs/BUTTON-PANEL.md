# Button panel: wiring guide

Two momentary buttons and two LEDs on the Raspberry Pi 5's 40-pin header give
the demo a physical control surface:

- **PROMPT** — a momentary button with a **green LED**. Press it to run a
  prompt. The LED is always lit — it marks the button, not the wire's state
  (when the board's sync is down the web panel falls back to simulation; the
  hardware button does nothing until the wire is back).
- **WORKLOAD** — a momentary button with a **red/green bicolor LED**. Each
  press toggles the workload between **honest (green)** and **tampered
  (red)**. The next PROMPT press runs whatever the LED shows. The state lives
  on the Spark, so it survives Pi reboots and shows correctly on every screen.

The daemon (`pi_button_panel.py`, systemd unit `interlock-buttons`) is already
installed and running — it is safe unwired, so you can wire at your leisure
and the buttons come alive as you connect them.

## Pinout

All pins are on the Pi's 40-pin header. "BCM" is the GPIO number the software
uses; "pin" is the physical header position (pin 1 is nearest the corner,
odd row is the inner row).

| Signal              | BCM | Physical pin | Goes to |
|---------------------|-----|--------------|---------|
| PROMPT button       | 17  | pin 11       | one leg of button 1 |
| PROMPT LED (green)  | 27  | pin 13       | LED 1 anode, through 330 Ω |
| WORKLOAD button     | 22  | pin 15       | one leg of button 2 |
| WORK LED green leg  | 23  | pin 16       | bicolor green anode, through 330 Ω |
| WORK LED red leg    | 24  | pin 18       | bicolor red anode, through 220–330 Ω |
| Ground              | —   | pins 9, 14, 20 (any) | buttons' other legs, LED cathodes |

```
             3v3  [ 1] [ 2]  5v
                  [ 3] [ 4]  5v
                  [ 5] [ 6]  GND
                  [ 7] [ 8]
             GND  [ 9] [10]        <- button grounds here is convenient
  BTN PROMPT ---> [11] [12]
  LED PROMPT ---> [13] [14]  GND   <- LED cathodes here
  BTN WORK   ---> [15] [16]  <--- LED WORK green
                  [17] [18]  <--- LED WORK red
                  [19] [20]  GND
```

## Wiring the buttons

Momentary buttons need **no resistor**: the GPIO uses an internal pull-up, so
wire one leg to the GPIO pin and the other leg to any ground pin. A press
pulls the line to ground. (On a 4-leg tactile switch, use two legs on the
same side, or the two diagonal legs, to be sure you're across the contact.)

If your buttons have built-in LEDs, the LED is a separate circuit from the
switch: wire the switch contacts as above and the LED as below.

## Wiring the LEDs

- **PROMPT green LED**: GPIO 27 (pin 13) → 330 Ω resistor → LED anode (long
  leg) → LED cathode → ground.
- **WORKLOAD bicolor LED**, depending on which type you have:
  - **3-pin, common cathode** (most common): the middle/common pin → ground.
    Green anode → 330 Ω → GPIO 23 (pin 16). Red anode → 220–330 Ω → GPIO 24
    (pin 18). (If your part is common *anode*, tell me — the daemon needs its
    polarity flipped, a two-line change.)
  - **2-pin bicolor** (antiparallel): wire one leg → 330 Ω → GPIO 23 and the
    other leg → GPIO 24 directly. The daemon already drives the two pins in
    opposition, so this works as-is: green one way, red the other.

One resistor per LED leg. 330 Ω is right for 3.3 V GPIO; the red leg can use
220 Ω if the red looks dim next to the green.

## How it talks

The daemon speaks plain HTTP to the Spark (nothing to install, works fully
offline on the hotspot):

- `POST http://10.42.0.1/panel/prompt` — start a run of the current workload
- `POST http://10.42.0.1/panel/workload` — toggle honest/tampered
- `GET  http://10.42.0.1/panel/state` — `{wire, running, workload}` (drives the LEDs)

The workload state lives on the **Spark**, and every run the server starts is
stamped honest-or-tampered in its `beat:start` — so the web panel banners
correctly for hardware-started runs and vice versa.

## Testing without hardware

From the Pi (or the Spark):

```sh
curl -s -X POST http://10.42.0.1/panel/workload   # toggle; watch any open web panel
curl -s -X POST http://10.42.0.1/panel/prompt     # same as pressing PROMPT
curl -s http://10.42.0.1/panel/state
```

Watch the daemon: `journalctl -u interlock-buttons -f` on the Pi. Restart it
after wiring changes: `sudo systemctl restart interlock-buttons`.

## If the LEDs take a minute to light after boot

The panel daemon (and the agent) start after `network-online.target`. The
FPGA wire port (`eth0`) carries raw frames, not IP -- NetworkManager will
wait its full 60 s timeout trying to DHCP it, and everything downstream
starts a minute late. Tell it the port has no IP to configure:

```sh
sudo nmcli connection modify "Wired connection 1" ipv4.method disabled ipv6.method disabled
```

NM then reports the port connected immediately (and stops broadcasting
DHCP onto the certified wire), `network-online` completes as soon as the
hotspot wifi is up, and the LEDs light within seconds of boot.

## Behavior notes

- A PROMPT press while the wire is down gets a fault banner on the web panel.
  The hardware path never falls back to simulation — only the browser can
  simulate.
- A PROMPT press mid-run is refused by the server's single-flight guard.
- The WORKLOAD toggle is an operator setting, not part of a run: it persists
  until pressed again, including across runs and Pi reboots.
