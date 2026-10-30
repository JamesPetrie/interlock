"""How a process that has a deadline sets itself up: a core of its own and
real-time priority where the OS allows (`pin`), its own sources of pauses
removed (`low_jitter`), and the choice of the pacer's core (`pacer_core`).
"""
from __future__ import annotations

import gc
import os


def low_jitter(touch_mb: int = 64) -> str:
    """Remove the process's own sources of pauses as far as the OS allows:
    freeze and stop the cyclic garbage collector, keep freed memory in the
    heap instead of returning it to the kernel, lock the memory where the
    limits allow, and pre-touch `touch_mb` of heap, so that growing into it
    takes no page faults. A pacer process under ILSTACK_LOWJITTER=1 and the
    kernel release always."""
    import ctypes
    notes = []
    gc.collect()
    gc.freeze()
    gc.disable()
    notes.append("gc off")
    try:
        libc = ctypes.CDLL(None)
        M_TRIM_THRESHOLD, M_MMAP_THRESHOLD = -1, -3
        libc.mallopt(M_TRIM_THRESHOLD, 1 << 30)
        libc.mallopt(M_MMAP_THRESHOLD, 1 << 30)
        notes.append("no trim")
        MCL_CURRENT, MCL_FUTURE = 1, 2
        if libc.mlockall(MCL_CURRENT | MCL_FUTURE) == 0:
            notes.append("mlockall")
    except (OSError, AttributeError):
        pass
    buf = bytearray(touch_mb << 20)
    for i in range(0, len(buf), 4096):
        buf[i] = 1
    del buf
    notes.append(f"{touch_mb} MB touched")
    return ", ".join(notes)


def pin(core: int | None) -> str:
    """Pin this process to a core and raise its priority where the OS allows."""
    notes = []
    if os.environ.get("ILSTACK_LOWJITTER"):
        notes.append(low_jitter())
    if core is not None and hasattr(os, "sched_setaffinity"):
        try:
            os.sched_setaffinity(0, {core})
            notes.append(f"pinned to core {core}")
        except OSError as e:
            notes.append(f"could not pin to core {core}: {e}")
    if hasattr(os, "sched_setscheduler"):
        try:
            os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(50))
            notes.append("SCHED_FIFO")
        except (OSError, AttributeError):
            pass
    return ", ".join(notes) or "no pinning available"


def pacer_core(spec) -> int | None:
    """The core for a pacer process. `auto` takes the highest core this
    process may run on and keeps this (main) process off it, so that the
    sender never shares the pacer's core; a number names the core; `none`
    leaves it to the OS."""
    if spec in (None, "none"):
        return None
    if spec != "auto":
        return int(spec)
    if not hasattr(os, "sched_getaffinity"):
        return None
    cpus = os.sched_getaffinity(0)
    core = max(cpus)
    if len(cpus) > 1:
        os.sched_setaffinity(0, cpus - {core})
    return core
