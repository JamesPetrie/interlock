# Flashing and testing the interlock on the DGX Spark bench

This is the companion to [`SETUP.md`](SETUP.md). `SETUP.md` takes a fresh cloud
VM to the point where `./build.sh` produces a bitstream
(`gateware/Libero_Project/designer/top/export/top.job`). This document picks up
there: how that `.job` gets **flashed onto the MPF300 board** and how the live
bitstream is **tested on real ethernet**.

All the scripts referenced below live in [`../bench/`](../bench) — see
[`bench/README.md`](../bench/README.md) for a one-line index and the two
bench-specific values you must adjust for your own hardware.

> **Provenance.** The procedure and scripts here were verified end-to-end on the
> project's DGX Spark bench (`spark-c191`). The bench-specific script copies also
> live on that machine at `~/fpe/`; this directory is the version-controlled copy
> so a collaborator can reproduce the workflow without reverse-engineering it off
> the box.

## The split: build on x86, flash + test on the Spark

Bitstreams are **built on an x86 Linux box** (Hetzner, in our setup) because
Microchip's Libero / FPExpress toolchain is **x86-only** and has no ARM build.
The **DGX Spark is ARM64**, so it cannot run Libero — but it is where the MPF300
board, the FlashPro5 programmer, and the two test NICs are physically cabled.

So the flow is always: **build the `.job` on x86 → copy it to the Spark → flash +
test on the Spark.** Everything below happens on the Spark unless it says
"(build host)".

### Why flashing needs a VM

FPExpress is x86-only, and the Spark is ARM64, so the flash runs inside an **x86
QEMU VM**. A command from the Spark host has to cross three namespaces before it
reaches the USB programmer:

```
host  (user `claude`, reached via `tailscale ssh claude@spark-c191`)
  └─ docker container `box64test`      (runs qemu-system-x86_64 as root; mounts ~/fpe and /dev/bus/usb)
       └─ QEMU x86 VM  "fpe-vm" (Ubuntu 22.04)   SSH on 127.0.0.1:2222  (ubuntu / ubuntu)
            ├─ /mnt/fpe  = 9p LIVE mirror of host ~/fpe   (mount tag `fpehost`)
            ├─ FlashPro USB programmer passed through     (USB 1514:2008 → FPExpress)
            └─ Libero/FPExpress at /mnt/fpe/Libero        (= host ~/fpe/Libero)
```

Two facts that make this confusing the first time:

1. **The job that gets flashed is `~/fpe/top.job` on the Spark host.** The VM sees
   it as `/mnt/fpe/top.job` over a live 9p passthrough — editing the host file
   changes what the VM flashes, no copy into the VM needed. (Verify with
   `sha256sum` on both ends.)
2. **The VM's SSH (`:2222`) is only reachable from inside `box64test`.** QEMU's
   user-mode `hostfwd=tcp::2222-:22` lives in the *container's* network namespace,
   so from the host shell `ssh -p 2222 127.0.0.1` is "connection refused". You go
   through the container. `sshpass` is not on the host; it is in `box64test` and in
   the `ilssh` helper image.

`claude` on the Spark has **docker but no sudo** — every privileged step runs in a
container.

## Bench topology

- **MPF300-EVAL-KIT:** FlashPro5 USB programmer enumerates as USB `1514:2008`.
  The two RJ45 ports are cabled to the two Spark NICs.
- **Spark NICs:** `enP7s7` (built-in) and `enxb8fbb3b1f53c` (USB dongle). Either
  can be the send or receive side; the tests exercise both directions.
  **Port 0** (cert / beacon egress, toward the prover frontend) has, at times,
  been on either NIC — cables have been swapped mid-project, so the test scripts
  try both and the per-test notes say which NIC showed the certs.
- **Docker containers on the Spark:**
  - `box64test` — the QEMU-VM runner. Mounts `~/fpe`→`/fpe` and `/dev/bus/usb`.
    Run `vmboot.sh` here.
  - `ilssh` (`ubuntu:24.04` + `sshpass` + `openssh-client`) — used with
    `--network container:box64test` to reach the guest's `:2222`.
  - `fpe`, `nettest` — Libero holder / plain `~/fpe` mounts (not the VM).

## Procedure

### 1. Build the `.job` (build host)

```
cd /root/fpga/interlock && ./build.sh        # ~22 min → gateware/.../export/top.job
```

### 2. Copy the `.job` to the Spark and verify

```
base64 -w0 gateware/Libero_Project/designer/top/export/top.job \
  | tailscale ssh claude@spark-c191 'base64 -d > ~/fpe/top.job'
# then compare sha256sum on both ends — they must match
```

Keep a backup before overwriting: `cp ~/fpe/top.job ~/fpe/top.job.bak-<tag>`.
Known-good fallbacks are kept in `~/fpe` (`known_good.job`, `prev_top.job`); if a
new job misbehaves, reflash a known-good one the same way.

### 3. Make sure the VM is up

The VM is **8 GB and gets OOM-killed under memory pressure from other agents** —
check `free -h` first and only boot it when there's headroom.

```
# Is qemu alive inside box64test?
docker exec box64test sh -c 'cat /proc/[0-9]*/comm 2>/dev/null | grep -q qemu && echo UP || echo DOWN'

# If DOWN: free the FlashPro from the host driver, then boot (guest up in ~90 s):
docker run --rm --privileged --pid=host ubuntu:24.04 bash -c 'rmmod ftdi_sio'   # ignore if absent
docker exec -d box64test bash /fpe/vmboot.sh
docker exec box64test bash /fpe/vmwait.sh                                        # waits for guest ssh
```

`bench/vmboot.sh` is the exact `qemu-system-x86_64` invocation (8 GB, `jammy.img`
guest, `hostfwd 2222`, USB passthrough `1514:2008`, 9p `~/fpe`→`/mnt/fpe`, serial
→ `~/fpe/console.log`). The serial console only logs boot and then goes quiet at
the login prompt — that is normal, not a hang.

### 4. Flash (FPExpress in the guest; ~15–17 min under TCG)

Two equivalent paths. The wrapper is easiest:

```
# From the Spark host — flash_drive.sh crosses host → box64test → VM:2222
bash ~/flash_drive.sh verify     # md5/ls of /mnt/fpe/top.job as the VM sees it
bash ~/flash_drive.sh launch     # runs bench/gprog_launch.sh in the VM (detached) → "LAUNCHED pid=NNNN"
bash ~/flash_drive.sh wait       # polls /tmp/prog.out → "Chain programming PASSED" | "FAILED"
bash ~/flash_drive.sh tail       # live tail of /tmp/prog.out + the FPExpress pid
```

Under the hood, inside the guest (all in `bench/`):

- `gprog_launch.sh` — mount `/mnt/fpe`, start Xvfb, run FPExpress
  `run_selected_actions` on `/mnt/fpe/top.job`, **detached**.
- `gprog_wait.sh` — poll `/tmp/prog.out` for `PASSED`/`FAILED`.
- `gprog.sh` — synchronous variant (mount + Xvfb + program, greps the result,
  1100 s timeout).
- `guest_run.sh` — first-time VM bring-up: 9p mount + apt deps + a `scan_chain`
  sanity check. Run this once on a fresh guest image.
- `launch_program.sh` / `do_program.sh` — older host-path variants that assume
  `/mnt/fpe` is already mounted. Prefer the `gprog_*` path via `flash_drive.sh`.
- `greset.sh` — runs a `DEVICE_INFO` action; handy to confirm the chain is seen.

After **PASSED**, the FPGA holds the new bitstream **independent of the VM** —
killing qemu does not stop forwarding. Re-test from the host (step 5).

### 5. Test the live bitstream (host NICs — no VM/USB needed)

The data-plane tests run entirely from the Spark host over the two NICs, in
containers (`claude` is in the docker group). They are independent of the flash
VM. Pick the tests that match what the current bitstream implements:

| Test | Script | What PASS looks like |
|---|---|---|
| **Forwarding + sanitization** | `bench/canon_fwd.sh [count] [len]` | forced-DST (`02:..:01/02`) frames captured on the far NIC — proves the frame traversed *and* was sanitized by the bridge (see the tcpdump caveat below) |
| **TYPE-frame drop** | `bench/type_drop.sh [send] [recv] [count] [len]` | LENGTH frames forward, EtherType `0x86DD` frames drop, LENGTH frames *still* forward after (no wedge) |
| **Spaced forwarding** | `bench/fwd_spaced_test.sh [count] [gap_ms]` | gentle forwarding probe safe for per-packet cert builds (no HMAC overrun) |
| **Cert egress (bulk)** | `bench/canon_certtest.sh [count]` | cert frames (`ilock-v5` payload) captured on the port-0 NIC |
| **Per-packet cert + verify** | `bench/cert_onebyone_test.sh [count] [gap_ms]` | one cert per packet; `cert_parse.py` verifies `tau == HMAC-SHA256(key, m)` and that each `overall` hash binds a known test packet |
| **Spaced per-packet cert** | `bench/cert_spaced_test.sh [count] [gap_ms]` | cert frames with the version+id markers, count ≈ packets fed |
| **Tick beacon** | `bench/canon_beacontest.sh [seconds]` | beacon frames (`ilbcn-v1`, DST `02:..:CB`) with a monotonically increasing bucket |
| **Bucket accept/drop** | `bench/bucket_silicon_test.sh [p0_iface] [send_iface]` | beacon-driven bucket accept/drop on the combined build |
| **Bucket-edge calibration** | `bench/calib_probe.py <iface> <buckets> <util> [--tuned]` | 0% miss with landing-error std in the tens of µs (flywheel PLL + FIRST_DELTA servo; see `bench/README.md` for tuning notes) |
| **Full-bandwidth burst** | `bench/burst_test.py <send> <far> <secs> [util] [flags]` | acceptance ≈ offered up to the NIC's clean-throughput ceiling; per-flag guidance in `bench/README.md` |

Example:

```
bash ~/fpe/canon_fwd.sh 3000 64
# PASS = both directions report sanitized-frames(forced DST) > 0 on the recv side.
```

> **tcpdump LLC caveat.** For 802.3 LENGTH frames, tcpdump parses the first 3
> payload bytes as an LLC header (DSAP/SSAP/control), so the `ILOCKFWD` payload
> renders as `CKFWD…` in the dump. `canon_fwd.sh`'s `ILOCKFWD-payload` counter
> therefore reads **0 even when forwarding works** — it greps for the literal
> `ILOCKFWD`, which the LLC parser has split. **The reliable signal is
> `sanitized-frames (forced DST) > 0`**; the intact payload is visible in the hex
> sample (`434b 4657 44…` = `CKFWD`). The same 3-byte LLC shift affects the cert
> tests (`ilock-v5` shows as `ck-v5…`). (Verified on the live bench 2026-07-05:
> 100 sanitized frames each direction, payload intact.)

### 6. UART telemetry (optional, host-side)

The Mi-V prints fabric telemetry on the FlashPro FTDI's UART (`/dev/ttyUSB0`,
115200 8N1). The catch: **the FTDI can be held by exactly one of** {host
`ftdi_sio`, qemu(guest), FPExpress(JTAG)}. The guest kernel can't load
`ftdi_sio`, so read on the **host** after releasing the USB from qemu (the
bitstream is persistent — killing the VM does not stop forwarding):

```
docker exec box64test pkill -f qemu-system        # release USB to the host
# stream traffic so counters move (background), then read the UART via a privileged container:
docker run --rm --privileged -v /dev:/dev -v /lib/modules:/lib/modules ubuntu:24.04 bash -c '
  command -v modprobe || (apt-get update -qq && apt-get install -y -qq kmod)
  modprobe ftdi_sio; echo 1514 2008 > /sys/bus/usb-serial/drivers/ftdi_sio/new_id
  sleep 2; stty -F /dev/ttyUSB0 115200 raw -echo; timeout 7 cat /dev/ttyUSB0'
```

`bench/read_uart.sh` is the guest-side equivalent (binds `ftdi_sio` inside the VM
and dumps the port). Console lines: `[poll]` (link bits), `[cfg]`, `[dbg]`
(reframe geometry + 32-bit frame_count), `[dbg2]`/`[dbg3]` (deframe accounting).

> **Important:** reading UART on the host leaves host `ftdi_sio` holding the FTDI,
> which blocks qemu USB passthrough. Before the next flash, `rmmod ftdi_sio`
> (privileged) and reboot the VM (step 3).

## Gotchas

- **VM OOM.** The 8 GB VM is killed under memory pressure. Check `free -h` before
  booting; don't kill another agent's job to make room.
- **`ftdi_sio` before a flash.** Host `ftdi_sio` loaded → qemu USB passthrough
  fails. `rmmod` it before flashing.
- **`~/fpe` is the working dir.** Always stage jobs and run bench scripts from
  `~/fpe` on the Spark host; the VM sees it as `/mnt/fpe` over 9p. `/fpe` on the
  host "not existing" is expected — it exists inside the containers/VM.
- **Interlock wedge.** After a heavy session the bridge can stop issuing certs
  while the NICs stay healthy — a reflash (steps 3–4) resets it.
- **Wire constants drift per build.** The `ilock-v5` / cert-length / port-0 mapping
  values baked into the test scripts track a specific bitstream. When the gateware
  changes the cert format, update the matching test/parse script (they are small
  and self-documenting).

## What is NOT in the repo

The bench itself is large binary state and is **not** version-controlled — only
the scripts and this runbook are. To stand up a new bench you reconstruct:

- the `box64test` container + the `jammy.img` x86 guest (Ubuntu 22.04 cloud image
  + `seed.iso` cloud-init, `ubuntu`/`ubuntu`),
- the `ilssh` helper image,
- the Libero/FPExpress install mounted at `~/fpe/Libero`.

Run `bench/guest_run.sh` once against a fresh guest to install the FPExpress
runtime dependencies and confirm `scan_chain` sees the board.
