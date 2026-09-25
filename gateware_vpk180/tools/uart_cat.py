#!/usr/bin/env python3
"""Read a serial console for N seconds and print what arrives (115200 8N1,
raw). For the Versal UARTs on the VPK180's FT4232H: ttyUSB1 / ttyUSB2.

    uart_cat.py /dev/ttyUSB1 20
"""
import os, select, sys, termios, time

dev, secs = sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
fd = os.open(dev, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
attr = termios.tcgetattr(fd)
attr[0] = 0; attr[1] = 0; attr[2] = termios.CS8 | termios.CREAD | termios.CLOCAL; attr[3] = 0
attr[4] = attr[5] = termios.B115200
termios.tcsetattr(fd, termios.TCSANOW, attr)
end = time.time() + secs
out = b""
while time.time() < end:
    r, _, _ = select.select([fd], [], [], 0.2)
    if r:
        try:
            chunk = os.read(fd, 4096)
        except BlockingIOError:
            continue
        out += chunk
        sys.stdout.write(chunk.decode(errors="replace")); sys.stdout.flush()
os.close(fd)
if not out:
    print("(no output on %s in %.0fs)" % (dev, secs))
