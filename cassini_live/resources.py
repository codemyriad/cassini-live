"""Read cgroup v2 budgets and pressure. Missing enforcement is not a safe budget."""

import logging
import math
import os
from pathlib import Path

from . import catalog

LOGGER = logging.getLogger(__name__)
MIB = 1024 * 1024
# Unjustified: reused from the diagnostic video's headroom constant (diagnostics.py). Measuring
# the supervisor's RSS with 10 tracks and the video on, then 1.5x the peak, would justify it.
SUPERVISOR_MARGIN_MIB = 128
_stat_warned = False


def _number(path):
    try:
        value = path.read_text().strip()
        return None if value == "max" else int(value)
    except (OSError, ValueError):
        return None


def _paths(root):
    paths = [root]
    try:
        for line in Path("/proc/self/cgroup").read_text().splitlines():
            if line.startswith("0::"):
                current = root / line[3:].lstrip("/")
                if ".." not in current.parts:
                    paths.extend(p for p in (current, *current.parents) if p.is_relative_to(root))
    except OSError:
        pass
    return list(dict.fromkeys(paths))


def _pressure(path, window="avg60"):
    try:
        for line in path.read_text().splitlines():
            if line.startswith("some "):
                value = float(dict(item.split("=") for item in line.split()[1:])[window])
                return value if math.isfinite(value) and 0 <= value <= 100 else None
    except (OSError, ValueError, KeyError):
        pass
    return None


def _pressure_total(path):
    """Cumulative "some" stall time in microseconds; None when unreadable. The averages above
    are clamped to a percentage, so this counter, differenced per tick, needs its own reader."""
    try:
        for line in path.read_text().splitlines():
            if line.startswith("some "):
                value = int(dict(item.split("=") for item in line.split()[1:])["total"])
                return value if value >= 0 else None
    except (OSError, ValueError, KeyError):
        pass
    return None


def _stat_memory(path):
    """Page cache charged to the cgroup (memory.stat "file") and the tmpfs part of it
    ("shmem", counted in "file" but only swappable, never reclaimable); (None, None) when
    unreadable. A memory.stat without a shmem line reports 0 for it."""
    try:
        stats = {}
        for line in (path / "memory.stat").read_text().splitlines():
            key, _, value = line.partition(" ")
            if key in ("file", "shmem"):
                stats[key] = int(value)
        if "file" in stats:
            return stats["file"], stats.get("shmem", 0)
    except (OSError, ValueError):
        pass
    return None, None


def snapshot(root=Path("/sys/fs/cgroup"), proc=Path("/proc")):
    global _stat_warned
    affinity = (
        len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count() or 1
    )
    quotas, memory, swap, pids = [], [], [], []
    memory_ratio = pids_ratio = 0.0
    throttled = periods = oom = 0
    throttled_usec = 0
    severity_available = True
    monitored = False
    unreadable = False
    memory_current = memory_file = memory_shmem = None
    binding = None
    paths = _paths(root)
    for path in paths:
        try:
            quota, period = (path / "cpu.max").read_text().split()
            if quota != "max" and int(quota) > 0 and int(period) > 0:
                quotas.append(int(quota) / int(period))
        except (OSError, ValueError):
            pass
        for name, dest in (("memory.max", memory), ("memory.swap.max", swap), ("pids.max", pids)):
            value = _number(path / name)
            if value is not None:
                dest.append(value)
        mem_max, mem_now = _number(path / "memory.max"), _number(path / "memory.current")
        pid_max, pid_now = _number(path / "pids.max"), _number(path / "pids.current")
        if mem_max and mem_now is not None:
            memory_ratio = max(memory_ratio, mem_now / mem_max)
        limit = math.inf if mem_max is None else mem_max
        # The fit check charges usage against the cgroup whose limit binds; on a tie the
        # outer one, whose usage includes everything below it. The paths come innermost
        # first, so depth breaks the tie rather than iteration order.
        if mem_now is not None and (binding is None or (limit, len(path.parts)) < binding):
            binding, memory_current = (limit, len(path.parts)), mem_now
            memory_file, memory_shmem = _stat_memory(path)
        if pid_max and pid_now is not None:
            pids_ratio = max(pids_ratio, pid_now / pid_max)
        if mem_max is not None and mem_now is None or pid_max is not None and pid_now is None:
            unreadable = True
        try:
            stats = dict(line.split() for line in (path / "cpu.stat").read_text().splitlines())
            throttled += int(stats.get("nr_throttled", 0))
            if "throttled_usec" in stats:
                throttled_usec += int(stats["throttled_usec"])
            elif path == root:
                severity_available = False
            periods += int(stats.get("nr_periods", 0))
            events = dict(
                line.split() for line in (path / "memory.events").read_text().splitlines()
            )
            oom += int(events["oom_kill"])
            monitored = True
        except (OSError, ValueError, KeyError):
            if mem_max is not None or pid_max is not None:
                unreadable = True
    meminfo = {}
    try:
        meminfo = {
            line.split(":")[0]: int(line.split()[1]) * 1024
            for line in (proc / "meminfo").read_text().splitlines()
        }
    except (OSError, ValueError, IndexError):
        pass
    cpu = min(quotas) if quotas else None
    cpu_pressure = [_pressure(proc / "pressure/cpu"), _pressure(root / "cpu.pressure")]
    cpu_recent = [
        _pressure(proc / "pressure/cpu", "avg10"),
        _pressure(root / "cpu.pressure", "avg10"),
    ]
    memory_pressure = [_pressure(proc / "pressure/memory"), _pressure(root / "memory.pressure")]
    psi_available = all(value is not None for value in cpu_pressure + memory_pressure)
    # The container's own cgroup is the deepest path: its stall time is what admission
    # differences. Under a cgroup namespace (docker) it is the root itself.
    own = max(paths, key=lambda p: len(p.parts))
    cpu_stall = _pressure_total(own / "cpu.pressure")
    if cpu_stall is None and own != root:
        cpu_stall = _pressure_total(root / "cpu.pressure")
    if memory_current is not None and memory_file is None and not _stat_warned:
        _stat_warned = True
        LOGGER.warning("memory.stat is unreadable; memory headroom counts page cache as used")
    return {
        "monitoringAvailable": monitored and not unreadable,
        "pressureAvailable": psi_available,
        "cpuPressure": max(cpu_pressure) if psi_available else None,
        "cpuPressureRecent": (
            max(cpu_recent) if psi_available and None not in cpu_recent else None
        ),
        "hostCpuPressure": _pressure(proc / "pressure/cpu", "avg10"),
        "cpuStallUsec": cpu_stall,
        "hostCpuStallUsec": _pressure_total(proc / "pressure/cpu"),
        "memoryPressure": max(memory_pressure) if psi_available else None,
        "effectiveCpus": min(affinity, cpu) if cpu else float(affinity),
        "cpuQuotaCpus": cpu,
        "affinityCpus": affinity,
        "memoryLimitBytes": min(memory) if memory else None,
        "memoryCurrentBytes": memory_current,
        "memoryFileBytes": memory_file,
        "memoryShmemBytes": memory_shmem,
        "memoryStatAvailable": memory_file is not None,
        "swapLimitBytes": min(swap) if swap else None,
        "pidsLimit": min(pids) if pids else None,
        "memoryFraction": memory_ratio,
        "pidsFraction": pids_ratio,
        "hostMemoryBytes": meminfo.get("MemTotal"),
        "hostAvailableBytes": meminfo.get("MemAvailable"),
        "throttledPeriods": throttled,
        "throttledUsec": throttled_usec if severity_available else None,
        "cpuPeriods": periods,
        "oomKills": oom,
    }


def limits(root=Path("/sys/fs/cgroup")):
    data = snapshot(root)
    return {k: data[k] for k in ("effectiveCpus", "memoryLimitBytes", "affinityCpus")}


def budget_problem(data, workers, engine_id="whistle"):
    cpu, memory, processes = data["cpuQuotaCpus"], data["memoryLimitBytes"], data["pidsLimit"]
    if not cpu or not memory or not processes or data["swapLimitBytes"] != 0:
        return "Set finite cgroup v2 CPU, memory and PID limits, and disable container swap"
    if cpu > data["affinityCpus"] / 2:
        return "Reserve at least half the available CPU capacity for the host"
    host_memory = data["hostMemoryBytes"]
    if not host_memory or memory > host_memory / 2:
        return "Reserve at least half the host memory outside the caption container"
    if processes > 256:
        return "The caption container PID limit must be at most 256"
    engine = catalog.get(engine_id)
    if engine.kind == "worker_pool":
        if not 1 <= workers <= min(4, max(1, int(cpu // 2))):
            return "Requested recognition workers exceed the CPU budget (maximum four)"
        if memory < (384 + workers * 192) * MIB:
            return "The memory budget is too small for the requested recognition workers"
    else:
        needs = engine.requirements
        if not 1 <= workers <= catalog.worker_ceiling(engine_id, cpu):
            return f"Requested simultaneous speakers exceed the CPU budget for {engine.name}"
        if cpu < needs.min_cpu_quota:
            return f"{engine.name} needs a CPU quota of at least {needs.min_cpu_quota:g} CPU"
        if memory < catalog.memory_floor_mib(engine_id, workers) * MIB:
            return (
                f"The memory budget is too small for {engine.name}: set at least "
                f"{_gib(catalog.memory_floor_mib(engine_id, workers))} GiB "
                f"({_gib(needs.recommended_memory_mib)} GiB recommended)"
            )
    return pressure_problem(data)


def _gib(mib):
    # Rounded up: the value is a requirement, so the shown figure must be enough.
    return f"{math.ceil(mib / 1024 * 10 - 1e-9) / 10:.1f}".removesuffix(".0")


def load_problem(data, engine_id):
    """Loading a shared model must not itself push the container into the 80% trip."""
    engine = catalog.get(engine_id)
    if engine.kind == "worker_pool":
        return None
    memory = data.get("memoryLimitBytes")
    current = data.get("memoryFraction", 0) * (memory or 0)
    if not memory or current + engine.requirements.model_mib * MIB > 0.8 * memory:
        return f"Not enough free container memory to load {engine.name}"
    return pressure_problem(data)


def headroom_mib(data):
    """Memory the container can still take before memory.max, in MiB; None when unknown.

    Page cache (memory.stat "file") is not charged: the kernel reclaims file pages before it
    kills, and the host drops the model's cache after a cold load (engine_host.py). Its shmem
    part (tmpfs, /dev/shm) stays charged: only swap could take it and swap is 0. Without
    memory.stat the whole of memory.current counts, pessimistic by the cache."""
    limit, current = data.get("memoryLimitBytes"), data.get("memoryCurrentBytes")
    if not limit or current is None:
        return None
    reclaimable = (data.get("memoryFileBytes") or 0) - (data.get("memoryShmemBytes") or 0)
    return (limit - (current - max(0, reclaimable))) // MIB


def start_problem(data, engine_id, rss_mib=None, workers=1):
    """Why an engine must not load now, or None: a start stays clear of the kernel's OOM
    killer, not of a fraction of the budget. Nothing consults this yet (0.8.0 step 6).

    rss_mib is the last resident size the engine's host reported; the catalog's model_mib
    stands in before a first load. Whistle's workers are spawned one at a time, so workers
    counts those still to start."""
    engine = catalog.get(engine_id)
    if data.get("memoryLimitBytes") is None:
        return "Set finite cgroup v2 memory limits"
    headroom = headroom_mib(data)
    if headroom is None:
        return "Container memory use cannot be read"
    needs = engine.requirements
    if engine.kind == "worker_pool":
        # model_mib 192 per worker: 130/190 MiB measured at one/two workers, rounded up.
        need = needs.model_mib * workers
    else:
        # The full allowance of MAX_STREAMS streams and a max_batch batch, so a loaded engine
        # fits its ceiling whatever admission later meters per speaker. RSS 944-965 MiB and
        # batch 45 measured (2026-10-15); stream 16 provisional, about 7 measured serially.
        need = (
            (rss_mib or needs.model_mib)
            + needs.stream_mib * catalog.MAX_STREAMS
            + needs.batch_mib * needs.max_batch
            + SUPERVISOR_MARGIN_MIB
        )
    if headroom < need:
        return f"Not enough memory to load {engine.name}: {need} MiB needed, {headroom} MiB free"
    return None


def pressure_problem(data):
    if not data.get("monitoringAvailable", False):
        return "Resource pressure counters cannot be read safely"
    if data["memoryFraction"] >= 0.8:
        return "Caption memory budget is nearly exhausted"
    if data["pidsFraction"] >= 0.8:
        return "Caption process/thread budget is nearly exhausted"
    available, total = data["hostAvailableBytes"], data["hostMemoryBytes"]
    if available is None or total is None:
        return "Host memory headroom cannot be established"
    if available < max(256 * MIB, total * 0.1):
        return "Host memory is under pressure"
    return None


def signature(data):
    return tuple(
        data[k]
        for k in ("cpuQuotaCpus", "affinityCpus", "memoryLimitBytes", "swapLimitBytes", "pidsLimit")
    )
