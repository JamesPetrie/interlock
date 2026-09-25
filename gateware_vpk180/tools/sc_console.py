#!/usr/bin/env python3
"""Scripted session on the VPK180 system controller's Linux console (the
ZU4 "eval-brd-sc-zynqmp" on the FT4232H's 4th channel, 115200 8N1).

    sc_console.py [-d /dev/ttyUSB3] [-u petalinux] [-p petalinux] CMD [CMD ...]

Logs in if a login prompt is seen, runs each CMD, prints the output, logs out.
No pyserial dependency (raw termios), so it runs on a bare build box."""
import argparse, os, select, sys, termios, time


def open_tty(path):
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    attr = termios.tcgetattr(fd)
    attr[0] = 0                       # iflag: raw
    attr[1] = 0                       # oflag
    attr[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    attr[3] = 0                       # lflag: no echo/canonical
    attr[4] = attr[5] = termios.B115200
    termios.tcsetattr(fd, termios.TCSANOW, attr)
    termios.tcflush(fd, termios.TCIOFLUSH)
    return fd


def read_until(fd, patterns, timeout):
    buf = b""
    end = time.time() + timeout
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            try:
                buf += os.read(fd, 4096)
            except BlockingIOError:
                pass
            for p in patterns:
                if p in buf:
                    return buf, p
    return buf, None


def send(fd, s):
    os.write(fd, s.encode())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-d", "--dev", default="/dev/ttyUSB3")
    ap.add_argument("-u", "--user", default="petalinux")
    ap.add_argument("-p", "--password", default="petalinux")
    ap.add_argument("-t", "--timeout", type=float, default=15.0)
    ap.add_argument("cmds", nargs="+")
    a = ap.parse_args()
    fd = open_tty(a.dev)
    prompt = (b"$ ", b"# ")
    send(fd, "\r\n")
    buf, hit = read_until(fd, (b"login:", b"Password:") + prompt, 5)
    if hit == b"login:" or hit is None:
        send(fd, a.user + "\r\n")
        buf, hit = read_until(fd, (b"Password:",) + prompt, 5)
    if hit == b"Password:":
        send(fd, a.password + "\r\n")
        buf, hit = read_until(fd, prompt + (b"Login incorrect", b"login:"), 10)
        if hit not in prompt:
            print("LOGIN FAILED:\n" + buf.decode(errors="replace")[-300:])
            return 1
    for cmd in a.cmds:
        marker = "__END_%d__" % int(time.time() * 1000)
        send(fd, cmd + "; echo " + marker + "\r\n")
        buf, hit = read_until(fd, (b"\n" + marker.encode() + b"\r\n",), a.timeout)  # marker at line start = real output, not the echo
        text = buf.decode(errors="replace")
        # drop the echoed command line and the marker echo
        lines = [l for l in text.splitlines() if marker not in l]
        if lines and lines[0].strip().endswith(cmd.strip()):
            lines = lines[1:]
        print("### %s\n%s" % (cmd, "\n".join(lines).strip()))
        if hit is None:
            print("### (timeout after %.0fs)" % a.timeout)
    send(fd, "exit\r\n")
    time.sleep(0.5)
    os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
