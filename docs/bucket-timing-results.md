# Bucket timing & bandwidth — on-silicon results (2026-07-20)

Measured on the MPF300 board via the Spark harness (`bench/calib_probe.py`,
`bench/burst_test.py`), against builds of this tree. Sender = plain Python +
`AF_PACKET` on the Spark's host NICs (`enP7s7` = PCIe r8127, second port =
USB r8152 dongle); no hardware timestamping, no RT scheduling needed.

## Sender timing vs the interlock's clock (FIRST_ARR feedback)

The sync-packet `FIRST_ARR` field closes the loop exactly as designed
(`docs/prod_canon_proc.md`): the flywheel + edge-fit estimator holds the
sender's phase against the interlock's 80 MHz timer to:

- **std ≈ 9 µs, p99 ≈ +16..21 µs, worst −62 µs** over a 100 s soak
  (100 ms-bucket build, one probe per bucket, PCIe NIC).

Key harness lessons (cost real debugging time):

- Sync **arrival times** at the host are batched/delayed (r8152/r8127
  moderation; USB adds ms-scale TX/RX latency shifts under load). Never
  anchor phase on arrivals: number buckets from the sync stream, estimate
  phase/rate from `FIRST_ARR` edge observations (`send_t0 − first_arr`),
  least-squares over a rolling window.
- The request canon's `prev_id` **never resets**; a fresh sender process
  restarting ids from 2 is silently 100%-rejected. The harness derives
  start ids from the µs epoch. The production frontend needs the same
  discipline (or a documented reset).
- In bidirectional runs, attach a cBPF filter (accept only 64-byte DATA
  frames) to the sync socket, or the opposite direction's forwarded flood
  drowns the listener.

## Bandwidth into 1 ms buckets (`TOP=prod BUCKET_MS=1` build)

Bursts of max-size canonical frames, declared-bucket stamped, guard 60 µs
(start) / 250 µs (tail slack), acceptance counted on the far NIC:

| direction | rate offered | accepted | notes |
|---|---|---|---|
| RSP solo (PCIe) | 606 Mb/s | 96.5 % | 12 s |
| REQ solo (USB) | 603 Mb/s | 95.4 % | content checks on |
| RSP + REQ simultaneously | ~97 Mb/s each | **RSP 1 drop / 240,000; REQ 2 drops / 239,976** | 30 s, ≈6·10⁻⁶ |
| (recomp build, same params) | 604 Mb/s | 99.6 % | 60 s soak, 0.397 % drop |

Drops at ≥600 Mb/s are send-start scatter clipping burst tails against the
bucket boundary (host-side, Python); at ~100 Mb/s per direction the margins
are so wide that drops are effectively zero — the production-traffic regime.
Full-line-rate **bidirectional** is limited by the host NICs (the USB dongle
under simultaneous ~600 Mb/s rx+tx), not by the interlock.

Builds used: `build/1ms-buckets` (recomp, job 409130a3…) and
`build/prod-1ms` (prod, job 60567a9d…) — both reproducible from main after
the TOP/BUCKET_MS knobs PR as `TOP=recomp|prod BUCKET_MS=1 ./build.sh`.
