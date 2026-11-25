"""Read cgroup v2 budgets and pressure. Missing enforcement is not a safe budget."""

import logging
import math
import os
from pathlib import Path

from . import catalog

LOGGER = logging.getLogger(__name__)
MIB = 1024 * 1024
# Unjustified: reused from the diagnostic video's headroom constant (diagnostics.py). Measuring
# the supervisor's RSS with safety.MAX_TRACKS (24) tracks and the video on, then 1.5x the
# peak, would justify it (a track adds 0.6-0.9 MiB locally, see safety.MAX_TRACKS).
SUPERVISOR_MARGIN_MIB = 128
# D-930: a default budget stands in for a missing kernel limit, so a store install runs
# without `docker update`. An administrator's limit always wins for its resource; defaults
# only fill gaps, so a configured install keeps today's behavior to the byte.
CPU_BUDGET_CAP = 4.0  # the runbook's --cpus=4; also capped at half the host's CPUs
PIDS_BUDGET = 128  # the runbook's --pids-limit; watched, never enforced by us
# Shown wherever we advise enforcement; matches docs/install.md and the runbook.
LIMITS_COMMAND = "docker update --cpus=4 --memory=2g --memory-swap=2g --pids-limit=128 <container>"
_stat_warned = False


def default_cpu_budget(affinity):
    """CPU budget when cpu.max is missing: at most the runbook's 4, at most half the
    reachable CPUs (advice() asks an administrator for the same), never below 1."""
    return max(1.0, min(CPU_BUDGET_CAP, affinity / 2))


def default_memory_budget(engine_id, host_bytes):
    """Memory budget when memory.max is missing: the engine's recommended container size,
    capped at half the host's RAM; the recommendation alone when the host cannot be read."""
    recommended = catalog.get(engine_id).requirements.recommended_memory_mib * MIB
    if host_bytes:
        return min(recommended, host_bytes // 2)
    return recommended


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
    memory_current = memory_file = memory_shmem = swap_current = None
    pids_current = None
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
            swap_current = _number(path / "memory.swap.current")
        if pid_now is not None:
            pids_current = max(pids_current or 0, pid_now)
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
    # Sizing follows the budget: the kernel quota when one is set, the default otherwise
    # (D-930). effectiveCpus is what every thread, worker and seat formula reads.
    cpu_budget = cpu if cpu else default_cpu_budget(affinity)
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
        "effectiveCpus": min(affinity, cpu_budget),
        "cpuQuotaCpus": cpu,
        "cpuBudgetCpus": cpu_budget,
        "cpuBudgetSource": "kernel" if cpu else "default",
        "affinityCpus": affinity,
        "memoryLimitBytes": min(memory) if memory else None,
        "memoryCurrentBytes": memory_current,
        "memoryFileBytes": memory_file,
        "memoryShmemBytes": memory_shmem,
        "memoryStatAvailable": memory_file is not None,
        "swapLimitBytes": min(swap) if swap else None,
        "swapCurrentBytes": swap_current,
        "pidsLimit": min(pids) if pids else None,
        "pidsBudget": min(pids) if pids else PIDS_BUDGET,
        "pidsBudgetSource": "kernel" if pids else "default",
        "pidsCurrent": pids_current,
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


def apply_budgets(data, engine_id):
    """A snapshot with D-930 budgets standing in for missing kernel limits, plus a `budget`
    block for /status. CPU and PID budgets are engine-free and already in snapshot();
    memory needs the engine (its recommended container size is the default), so it is
    filled here, and `memoryLimitBytes` carries the budget so every fit and headroom
    reader works unchanged. The kernel's own value stays under memoryKernelLimitBytes.
    Idempotent, and tolerant of partial snapshots (tests, old journal readers)."""
    data = dict(data)
    affinity = data.get("affinityCpus") or 1
    cpu = data.get("cpuQuotaCpus")
    cpu_budget = cpu if cpu else data.get("cpuBudgetCpus") or default_cpu_budget(affinity)
    data["cpuBudgetCpus"], data["cpuBudgetSource"] = cpu_budget, "kernel" if cpu else "default"
    data["effectiveCpus"] = min(affinity, cpu_budget)
    if "memoryKernelLimitBytes" not in data:
        data["memoryKernelLimitBytes"] = data.get("memoryLimitBytes")
    memory = data["memoryKernelLimitBytes"]
    memory_budget = memory or default_memory_budget(engine_id, data.get("hostMemoryBytes"))
    data["memoryLimitBytes"] = memory_budget
    data["memoryBudgetSource"] = "kernel" if memory else "default"
    pids = data.get("pidsLimit")
    pids_budget = pids if pids else PIDS_BUDGET
    data["pidsBudget"], data["pidsBudgetSource"] = pids_budget, "kernel" if pids else "default"
    swap = data.get("swapLimitBytes")
    data["budget"] = {
        "cpus": cpu_budget,
        "cpuSource": data["cpuBudgetSource"],
        "memoryMiB": int(memory_budget // MIB),
        "memorySource": data["memoryBudgetSource"],
        "pids": pids_budget,
        "pidsSource": data["pidsBudgetSource"],
        "swapLimited": swap == 0,
        "kernelLimits": {"cpus": cpu, "memoryBytes": memory, "pids": pids, "swapBytes": swap},
    }
    return data


def static_problem(data, workers, engine_id="whistle"):
    """Why this container can never run the engine as configured, or None.

    Since D-930, missing kernel limits no longer block: a default budget stands in
    (apply_budgets) and advice() names the command that enforces the real thing. What
    still blocks is a configuration no budget can satisfy: a worker count over the CPU
    ceiling, the engine's memory floor over the budget, and a CPU budget under the
    engine's minimum. For Whistle the minimum blocks only a default budget: an
    administrator's explicit quota keeps the pre-D-930 behavior."""
    data = apply_budgets(data, engine_id)
    cpu, memory = data["cpuBudgetCpus"], data["memoryLimitBytes"]
    engine = catalog.get(engine_id)
    needs = engine.requirements
    if engine.kind == "worker_pool":
        if data["cpuBudgetSource"] == "default" and cpu < needs.min_cpu_quota:
            return (
                f"{engine.name} needs {needs.min_cpu_quota:g} CPUs and this host spares "
                f"only {cpu:g} without a CPU limit: set one ({LIMITS_COMMAND}) "
                "or switch to Nemotron"
            )
        if not 1 <= workers <= catalog.worker_ceiling(engine_id, cpu):
            return "Requested recognition workers exceed the CPU budget (maximum four)"
        if memory < (384 + workers * 192) * MIB:
            return "The memory budget is too small for the requested recognition workers"
        return None
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
    return None


def advice(data):
    """Installation advice that never blocks captions (0.8.0 Decision 5, widened by D-930):
    missing kernel limits are advised back with the command that sets them, and the
    comfort rules stay advice. Reads kernel values, so it accepts raw or budget-applied
    snapshots alike."""
    notes = []
    cpu, processes = data.get("cpuQuotaCpus"), data.get("pidsLimit")
    memory = data.get("memoryKernelLimitBytes", data.get("memoryLimitBytes"))
    swap = data.get("swapLimitBytes")
    if not cpu or not memory or not processes:
        notes.append(
            "No enforced container limits; captions run on a default budget. Kernel "
            f"limits protect the call even if captions misbehave: {LIMITS_COMMAND}"
        )
    if swap != 0:
        notes.append(
            "Disable container swap (set --memory-swap equal to --memory): a swapped "
            "caption engine stalls and its paging slows the whole host"
        )
    if cpu and cpu > (data.get("affinityCpus") or 1) / 2:
        notes.append("Reserve at least half the available CPU capacity for the host")
    host_memory = data.get("hostMemoryBytes")
    if memory and (not host_memory or memory > host_memory / 2):
        notes.append("Reserve at least half the host memory outside the caption container")
    if processes and processes > 256:
        notes.append("Keep the caption container PID limit at most 256")
    return notes


def _gib(mib):
    # Rounded up: the value is a requirement, so the shown figure must be enough.
    return f"{math.ceil(mib / 1024 * 10 - 1e-9) / 10:.1f}".removesuffix(".0")


def charged_bytes(data):
    """Memory the container holds that the kernel cannot reclaim; None when unknown.

    Page cache (memory.stat "file") is not charged: the kernel reclaims file pages before it
    kills, and the host drops the model's cache after a cold load (engine_host.py). Its shmem
    part (tmpfs, /dev/shm) stays charged: only swap could take it and swap is 0. Without
    memory.stat the whole of memory.current counts, pessimistic by the cache."""
    current = data.get("memoryCurrentBytes")
    if current is None:
        return None
    reclaimable = (data.get("memoryFileBytes") or 0) - (data.get("memoryShmemBytes") or 0)
    return current - max(0, reclaimable)


def headroom_mib(data):
    """Memory the container can still take before memory.max, in MiB; None when unknown."""
    limit, charged = data.get("memoryLimitBytes"), charged_bytes(data)
    if not limit or charged is None:
        return None
    return (limit - charged) // MIB


def start_problem(data, engine_id, rss_mib=None, workers=1):
    """Why an engine must not load now, or None: a start stays clear of the kernel's OOM
    killer, not of a fraction of the budget.

    rss_mib is the last resident size the engine's host reported; the catalog's model_mib
    stands in before a first load. Whistle's workers are spawned one at a time, so workers
    counts those still to start. Without a kernel memory limit the D-930 default budget
    bounds the fit, so a store install loads without `docker update`."""
    engine = catalog.get(engine_id)
    data = apply_budgets(data, engine_id)
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


class BudgetWatch:
    """Observe-only D-930 counters, one tick() per safety-monitor second: what budget
    enforcement would have done, so a staging run can justify (or drop) each trip before
    any of them may pause captions. Nothing here acts; /status reports it. Counted: ticks
    with unreclaimable memory over the budget, ticks with the process count at or over the
    PID budget, and ticks where the container's own swap use grew."""

    def __init__(self):
        self.memory_over_ticks = 0
        self.memory_peak_mib = None
        self.pids_high_ticks = 0
        self.pids_peak = None
        self.swap_grew_ticks = 0
        self._swap_previous = None

    def tick(self, data):
        """Feed one budget-applied snapshot (apply_budgets); unreadable inputs skip their
        signal rather than failing the monitor tick."""
        charged = charged_bytes(data)
        if charged is not None:
            self.memory_peak_mib = max(self.memory_peak_mib or 0, charged // MIB)
            budget = data.get("memoryLimitBytes")
            if budget and charged > budget:
                self.memory_over_ticks += 1
        pids = data.get("pidsCurrent")
        if pids is not None:
            self.pids_peak = max(self.pids_peak or 0, pids)
            budget = data.get("pidsBudget")
            if budget and pids >= budget:
                self.pids_high_ticks += 1
        swap = data.get("swapCurrentBytes")
        if swap is not None:
            if self._swap_previous is not None and swap > self._swap_previous:
                self.swap_grew_ticks += 1
            self._swap_previous = swap

    def status(self):
        return {
            "memoryOverBudgetTicks": self.memory_over_ticks,
            "memoryPeakMiB": self.memory_peak_mib,
            "pidsHighTicks": self.pids_high_ticks,
            "pidsPeak": self.pids_peak,
            "swapGrewTicks": self.swap_grew_ticks,
            "swapCurrentMiB": (
                None if self._swap_previous is None else self._swap_previous // MIB
            ),
        }
