#!/usr/bin/env python3
"""Optical status of the VPK180's QSFP-DD modules (CMIS page 11h) via the system
controller console. Run on the box that has the SC's /dev/ttyUSB3.

    qsfp_lights.py [--cages 1 2 3 4] [--wake N ...] [--settle S]

--wake N  clears LowPwrAllowRequestHW (lower page byte 26 = 0) on cage N first:
          the module goes ModuleReady, activates its data path and turns its
          lasers on (all lanes not TxDisabled in page 10h byte 130).
Prints, per cage: module state, DP state per lane, TxDisable, OutputStatus,
TxCDRLOL/RxLOS/RxCDRLOL, and TX/RX optical power per lane in mW."""
import argparse, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODSEL = {1: 0x6e20, 2: 0x6d20, 3: 0x6b20, 4: 0x6720}   # TCA6416A word, MODSELL low = selected
RESTORE = 0x6f20
SUDO = "echo petalinux | sudo -S"
I2C = "18 0x50"

def sel(c):  return f"{SUDO} sc_app -c setoutioexp -t TCA6416A -v {MODSEL[c]:#06x} >/dev/null 2>&1"
def get(reg): return f"$({SUDO} i2cget -y {I2C} {reg:#04x} 2>&1)"
def page(p):  return f"{SUDO} i2cset -y {I2C} 0x7f {p:#04x} >/dev/null 2>&1"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cages", type=int, nargs="+", default=[1, 2, 3, 4])
    ap.add_argument("--wake", type=int, nargs="*", default=[])
    ap.add_argument("--settle", type=float, default=15.0, help="seconds after wake before reading")
    ap.add_argument("--dev", default="/dev/ttyUSB3")
    ap.add_argument("--watch", type=float, default=0, help="poll RxLOS/RX power for this many seconds, report changes")
    a = ap.parse_args()
    if a.watch:
        return watch(a)
    parts = []
    for c in a.wake:
        parts.append(f"{sel(c)}; {SUDO} i2cset -y {I2C} 0x1a 0x00 2>&1; echo WOKE{c}")
    if a.wake:
        parts.append(f"sleep {a.settle:.0f}")
    for c in a.cages:
        parts.append(f"{sel(c)}; echo \"== CAGE{c} state={get(0x03)} lowpwr={get(0x1a)}\"; {page(0x10)}; echo \"txdis={get(0x82)}\"; "
                     f"{page(0x11)}; {SUDO} i2cdump -y -r 0x80-0xc9 {I2C} b 2>&1 | tail -n +2")
    parts.append(f"{SUDO} sc_app -c setoutioexp -t TCA6416A -v {RESTORE:#06x} >/dev/null 2>&1; echo ALLDONE")
    cmd = "; ".join(parts)
    to = str(int(60 + a.settle + 10 * len(a.cages)))
    r = subprocess.run([sys.executable, os.path.join(HERE, "sc_console.py"), "-d", a.dev, "-t", to, cmd],
                       capture_output=True, text=True, timeout=600)
    out = r.stdout + r.stderr
    if "ALLDONE" not in out:
        print(out); sys.exit("SC command did not finish")
    cur = None; cages = {}
    for line in out.splitlines():
        m = re.match(r"== CAGE(\d) state=(\S+) lowpwr=(\S+)", line)
        if m:
            cur = cages.setdefault(int(m.group(1)), {"state": m.group(2), "lowpwr": m.group(3), "mem": {}}); continue
        m = re.match(r"txdis=(\S+)", line)
        if m and cur is not None: cur["txdis"] = m.group(1); continue
        m = re.match(r"^([0-9a-f]{2}): ((?:[0-9a-fX]{2} ?)+)", line)
        if m and cur is not None:
            base = int(m.group(1), 16)
            for i, b in enumerate(m.group(2).split()):
                if b != "XX": cur["mem"][base + i] = int(b, 16)
    STATES = {1: "LowPwr", 2: "PwrUp", 3: "Ready", 4: "PwrDn", 5: "Fault"}
    for c in a.cages:
        d = cages.get(c)
        print(f"QSFPDD{c}:", end=" ")
        if not d or d["state"].startswith("Error") or len(d["mem"]) < 74:
            print("no module / no i2c response", "" if not d else f"({d['state']})"); continue
        m = d["mem"]; st = int(d["state"], 16)
        print(f"module {STATES.get((st >> 1) & 7, st):6s} lowpwr_req={d['lowpwr']} txdisable={d.get('txdis','?')} "
              f"DPstate={''.join(f'{m[128+i]:02x}' for i in range(4))} "
              f"OutStatRx={m[132]:02x} OutStatTx={m[133]:02x} TxLOS={m[135]:02x} TxCDRLOL={m[136]:02x} "
              f"RxLOS={m[147]:02x} RxCDRLOL={m[148]:02x}")
        tx = [(m[154 + 2*i] << 8 | m[155 + 2*i]) / 10000 for i in range(8)]
        rx = [(m[186 + 2*i] << 8 | m[187 + 2*i]) / 10000 for i in range(8)]
        print("   lane   : " + " ".join(f"{i+1:>6d}" for i in range(8)))
        print("   TX mW  : " + " ".join(f"{v:6.2f}" for v in tx))
        print("   RX mW  : " + " ".join(f"{v:6.2f}" for v in rx) +
              ("   <-- LIGHT IN on lanes " + ",".join(str(i+1) for i in range(8) if rx[i] > 0.01) if any(v > 0.01 for v in rx) else "   (dark)"))

def watch(a):
    """Poll RxLOS + RX power of the selected cages for a.watch seconds (~2 s/sample)."""
    body = " ".join(f"{sel(c)}; {page(0x11)}; echo \"W c={c} t=$SECONDS los={get(0x93)} rx=$({SUDO} i2cdump -y -r 0xba-0xc9 {I2C} b 2>&1 | tail -n +2 | cut -c5-52 | tr '\\n' ' ')\";"
                    for c in a.cages)
    cmd = (f"end=$((SECONDS+{int(a.watch)})); while [ $SECONDS -lt $end ]; do {body} done; "
           f"{SUDO} sc_app -c setoutioexp -t TCA6416A -v {RESTORE:#06x} >/dev/null 2>&1; echo ALLDONE")
    r = subprocess.run([sys.executable, os.path.join(HERE, "sc_console.py"), "-d", a.dev, "-t", str(int(a.watch) + 60), cmd],
                       capture_output=True, text=True, timeout=int(a.watch) + 120)
    out = r.stdout + r.stderr
    if "ALLDONE" not in out:
        print(out); sys.exit("SC watch did not finish")
    n = 0; lit = {}
    for line in out.splitlines():
        m = re.match(r"W c=(\d) t=(\d+) los=(\S+) rx=(.*)", line)
        if not m: continue
        n += 1; c = int(m.group(1)); by = m.group(4).split()
        try:
            rx = [(int(by[2*i], 16) << 8 | int(by[2*i+1], 16)) / 10000 for i in range(8)]
        except (ValueError, IndexError):
            print(f"  t={m.group(2)}s cage {c}: unparsable ({m.group(4)[:40]})"); continue
        on = [f"L{i+1}={rx[i]:.2f}mW" for i in range(8) if rx[i] > 0.01]
        key = (c, tuple(on), m.group(3))
        if key not in lit:
            lit[key] = m.group(2)
            print(f"  t={m.group(2):>3s}s QSFPDD{c}: RxLOS={m.group(3)} " + (" ".join(on) if on else "all lanes dark"))
    print(f"{n} samples; " + ("no light seen on any lane" if not any(k[1] for k in lit) else "LIGHT SEEN, see above"))

if __name__ == "__main__":
    main()
