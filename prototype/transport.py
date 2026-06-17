"""Length-prefixed message framing over a TCP socket.

The V1/V2 transport: one persistent connection, each message a 4-byte
big-endian length followed by that many bytes. This is the framing that
carries over to the raw-ethernet V3 step (length is already the first
header field); only the bytes underneath change.
"""
import struct


def send_msg(sock, data):
    sock.sendall(struct.pack(">I", len(data)) + data)


def recv_msg(sock):
    n = struct.unpack(">I", _recvn(sock, 4))[0]
    return _recvn(sock, n)


def _recvn(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed")
        buf += chunk
    return buf
