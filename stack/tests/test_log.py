import pytest

from ilstack import packet as P
from ilstack.crypto import PairKeys
from ilstack.log import FileLog, MemoryLog

KC = bytes(range(32))


def packets():
    r0 = P.Traffic(P.Id(P.INBOUND, 5, 2), P.Id(P.INBOUND, 5, 2), KC, b"abcd")
    r1 = P.Traffic(P.Id(P.INBOUND, 5, 0), P.Id(P.INBOUND, 5, 0), KC, b"")          # arrives out of slot order
    s0 = P.Traffic(P.Id(P.OUTBOUND, 6, 0), r0.id, payload=bytes(1436))
    return r0, r1, s0


def check(log):
    r0, r1, s0 = packets()
    assert log.get(r0.id) == r0.pack() and log.get(s0.id) == s0.pack()
    assert log.get(P.Id(P.INBOUND, 5, 1)) is None
    assert log.bucket(P.INBOUND, 5) == [r1.pack(), r0.pack()]                    # slot order
    assert log.bucket(P.OUTBOUND, 6) == [s0.pack()] and log.bucket(P.OUTBOUND, 5) == []
    assert [c.bkt_start for c in log.certificates] == [0, 10]
    assert log.counter(PairKeys(bytes(32)).pair(7).commit) == 7
    assert log.counter(KC) is None


def fill(log):
    r0, r1, s0 = packets()
    for pkt in (r0, r1, s0):
        assert log.record(pkt.pack()) == pkt
    for start in (0, 10):
        log.certificate(P.Certificate(6, 0, start, 10, bytes(16), bytes(32), bytes(32), bytes(32)))
    log.key(PairKeys(bytes(32)).pair(7))


def test_memory_log():
    log = MemoryLog()
    fill(log)
    check(log)
    with pytest.raises(ValueError):
        log.record(P.Sync(P.INBOUND, 1).pack())


def test_file_log_survives_reopen(tmp_path):
    log = FileLog(str(tmp_path))
    fill(log)
    check(log)
    assert len(log) == 3
    log.close()
    again = FileLog(str(tmp_path))
    check(again)
    again.record(P.Traffic(P.Id(P.INBOUND, 7, 0), P.Id(P.INBOUND, 7, 0), KC, b"more").pack())
    assert len(again) == 4 and again.bucket(P.INBOUND, 7)[0].endswith(b"more")
    again.close()
