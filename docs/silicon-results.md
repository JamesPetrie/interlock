# Bucket header + beacon: silicon results (2026-06-17)

Combined build (`feature/bucket-beacon` = header + beacon, `.job` sha `c43047b9`)
flashed to the MPF300 (PROGRAM PASSED, timing met all corners) and tested on the
Spark host NICs. Port-0 (cert + beacon egress) = `enP7s7`.

## ✅ Validated on silicon

| Check | Result |
|---|---|
| Boot + both PHY links | up immediately (`carrier=1`) |
| Forwarding (both directions) | works — 100 sanitized forced-DST frames each way |
| Cert egress | empty-bucket certs egress port-0 (DST `02:..:CE`, 140 B) |
| **Tick beacon** | **251 beacons in 4 s** on port-0, DST `02:..:CB`, magic `ilbcn-v1`, `iid=7`, `period_ns=1000000`, `stride=16`, **bucket advances by exactly 16 per beacon, monotonic** (e.g. 422896→426896). Port-1: 0. |

So **design-A clock distribution is live on real silicon** — the interlock broadcasts
its bucket counter, ticking at 1 ms, and the prover can read it to sync. This was the
primary goal of this build.

## Bucket accept/drop — sim-validated; not reproduced from the host on silicon

The exact-match bucket logic (declare-correct → fold; wrong → drop) is validated
**byte-exact in simulation**: `tb/test_interlock_tap` 4/4 including
`wrong_bucket_dropped`, plus `prototype/test_protocol.py` 10/10.

On silicon, the host-driven accept/drop test (`bucket_silicon_test.py`: sniff
beacons → declare the predicted bucket → check certs fold) was **inconclusive**:
both the accept phase (predicted bucket, even with a ±spread) and the drop phase
(absurd bucket) produced **0 non-empty certs** — every captured cert equals the
all-empty value for `N=8`. Since *nothing* folded in *either* phase, this is **not**
a bucket issue: **host-sent wire packets are not being folded into certs at all**.

This is a **pre-existing harness limitation, independent of the bucket header**: the
cert build's egress test (`canon_certtest.sh`) only ever verified that cert frames
*appear*, never that host traffic *folds* into them. The interlock core samples the
tapped forward stream only when idle, so host-driven folding was never validated on
silicon. Reproducing it needs a separate tap/timing investigation (or driving the
cores from the real prover path), out of scope for the bucket header.

**Net:** the bucket *logic* is proven (sim); the *beacon* is proven on silicon; the
on-silicon closed-loop accept/drop awaits host→core folding being made to work.

## Gotcha for future tests

Beacon and cert DSTs (`02:..:CB`, `02:..:CE`) are locally-administered **unicast**
addresses (not this NIC's MAC, not multicast), so a raw `AF_PACKET` capture must
enable **promiscuous mode** (`setsockopt(SOL_PACKET, PACKET_ADD_MEMBERSHIP,
PACKET_MR_PROMISC)`) or it silently sees nothing. `tcpdump` sets promisc itself.

## State left on the hardware

- Board re-flashed to the **known-good cert build** (`1231a06f`) — working baseline.
- `.job`s on the Spark: `top.job` = known-good, `combined.job` = header+beacon
  (`c43047b9`), `eth_sanitize.job` = Peter's (`c0530c05`), `known_good.job`.
- Test scripts on the Spark: `canon_beacontest.sh` + `canon_beacon_parse.py`
  (beacon capture/parse), `bucket_silicon_test.{sh,py}` (accept/drop).
