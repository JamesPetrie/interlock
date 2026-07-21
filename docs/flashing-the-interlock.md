# Flashing an FPGA job to the interlock (DGX Spark bench)

The DGX Spark is **ARM64**, but Microchip's Libero / FPExpress programming tools are
**x86-only**. So the flash runs inside an **x86 QEMU VM**, and a command from the host has
to cross three namespaces before it reaches the FlashPro programmer:

```
host  (user `claude`, reached via `tailscale ssh claude@spark-c191`)
  └─ docker container `box64test`        (runs qemu-system-x86_64 as root; has sshpass + ssh)
       └─ QEMU x86 VM  "fpe-vm" (Ubuntu 22.04)   SSH on 127.0.0.1:2222  (user/pass: ubuntu / ubuntu)
            ├─ /mnt/fpe  = 9p LIVE mirror of host ~/fpe   (mount tag `fpehost`)
            ├─ FlashPro USB programmer passed through     (USB VID 0x1514 → FPExpress)
            └─ Libero at /mnt/fpe/Libero                  (= host ~/fpe/Libero)
```

## The two facts that make it confusing

1. **The job that actually gets flashed is `~/fpe/top.job` on the host.** The VM sees it as
   `/mnt/fpe/top.job` over a live 9p passthrough, so editing the host file changes what the
   VM flashes — no copy into the VM needed. (Verify: `md5sum ~/fpe/top.job` on the host ==
   `md5sum /mnt/fpe/top.job` in the VM.)
2. **The VM's SSH (`:2222`) is only reachable from inside `box64test`.** QEMU's user-mode
   `hostfwd=tcp::2222-:22` lives in the *container's* network namespace, so from the host
   shell `ssh -p 2222 127.0.0.1` is "connection refused". You must go through the container:
   ```
   docker exec box64test bash -lc "sshpass -p ubuntu ssh -p 2222 \
     -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null ubuntu@127.0.0.1 '<cmd>'"
   ```
   `sshpass` is **not** installed on the host; it **is** in `box64test`.

## Procedure — flash a new job `NEWJOB`

`~/flash_drive.sh` wraps the namespace chain (subcommands `verify` / `launch` / `wait` / `tail`).

```
# 1. Stage on the host (this is the live source the VM flashes). Keep a backup.
cp ~/fpe/top.job ~/fpe/top.job.bak-$(date +%s 2>/dev/null || echo old)
cp NEWJOB        ~/fpe/top.job

# 2. Confirm the VM sees it (md5 must equal NEWJOB).
bash ~/flash_drive.sh verify

# 3. Launch FPExpress detached inside the VM.
bash ~/flash_drive.sh launch          # -> "LAUNCHED pid=NNNN"

# 4. Wait for the verdict (polls /tmp/prog.out in the VM).
bash ~/flash_drive.sh wait            # -> "Chain programming PASSED"  (or FAILED)
bash ~/flash_drive.sh tail            # live: /tmp/prog.out + the FPExpress pid
```

After **PASSED**, the FPGA holds the new bitstream independent of the VM. Re-test forwarding
from the host (e.g. send one inference packet and confirm a certificate comes back).

## Scripts (in `~/fpe`, run INSIDE the VM as root)

- `gprog_launch.sh` — mount `/mnt/fpe` (9p), start Xvfb, run `FPExpress … run_selected_actions`
  on `/mnt/fpe/top.job`, **detached** (survives the SSH session).
- `gprog_wait.sh` — poll `/tmp/prog.out` for `Chain programming PASSED|FAILED`.
- `gprog.sh` — synchronous variant (mount + Xvfb + program, greps the result, 1100 s timeout).
- `guest_run.sh` — first-time VM bring-up (9p mount + apt deps) plus a `scan_chain` sanity check.
- `do_program.sh` / `launch_program.sh` — older host-path variants that assume `/mnt/fpe` is
  already present; prefer the `gprog_*` scripts via `flash_drive.sh`.

## Gotchas

- If `/fpe` "doesn't exist" on the host, that's expected — it lives inside `box64test` (a bind
  of `~/fpe`) and as the VM's `/mnt/fpe`. **Always edit jobs in `~/fpe`.**
- VM serial console = host `~/fpe/console.log`; it only logs boot and goes quiet at the login
  prompt — that is normal, not a hang. The VM is alive if `~/fpe/jammy.img` mtime is recent.
- Job backups kept in `~/fpe`: `known_good.job`, `prev_top.job`, `top.job.*-bak`. If a new job
  misbehaves, reflash `known_good.job` the same way.
- Interlock wedge: after a heavy session the bridge can stop issuing certs (NICs stay healthy)
  — a reflash via this procedure resets it. See `app/README.md`.
