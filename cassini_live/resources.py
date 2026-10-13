"""Read cgroup v2 budgets and pressure. Missing enforcement is not a safe budget."""

import math
import os
from pathlib import Path

MIB = 1024 * 1024


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


def _pressure(path):
    try:
        for line in path.read_text().splitlines():
            if line.startswith("some "):
                value = float(dict(item.split("=") for item in line.split()[1:])["avg60"])
                return value if math.isfinite(value) and 0 <= value <= 100 else None
    except (OSError, ValueError, KeyError):
        pass
    return None


def snapshot(root=Path("/sys/fs/cgroup"), proc=Path("/proc")):
    affinity = (
        len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count() or 1
    )
    quotas, memory, swap, pids = [], [], [], []
    memory_ratio = pids_ratio = 0.0
    throttled = periods = oom = 0
    monitored = False
    unreadable = False
    for path in _paths(root):
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
        if pid_max and pid_now is not None:
            pids_ratio = max(pids_ratio, pid_now / pid_max)
        if mem_max is not None and mem_now is None or pid_max is not None and pid_now is None:
            unreadable = True
        try:
            stats = dict(line.split() for line in (path / "cpu.stat").read_text().splitlines())
            throttled += int(stats.get("nr_throttled", 0))
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
    memory_pressure = [_pressure(proc / "pressure/memory"), _pressure(root / "memory.pressure")]
    psi_available = all(value is not None for value in cpu_pressure + memory_pressure)
    return {
        "monitoringAvailable": monitored and not unreadable,
        "pressureAvailable": psi_available,
        "cpuPressure": max(cpu_pressure) if psi_available else None,
        "memoryPressure": max(memory_pressure) if psi_available else None,
        "effectiveCpus": min(affinity, cpu) if cpu else float(affinity),
        "cpuQuotaCpus": cpu,
        "affinityCpus": affinity,
        "memoryLimitBytes": min(memory) if memory else None,
        "swapLimitBytes": min(swap) if swap else None,
        "pidsLimit": min(pids) if pids else None,
        "memoryFraction": memory_ratio,
        "pidsFraction": pids_ratio,
        "hostMemoryBytes": meminfo.get("MemTotal"),
        "hostAvailableBytes": meminfo.get("MemAvailable"),
        "throttledPeriods": throttled,
        "cpuPeriods": periods,
        "oomKills": oom,
    }


def limits(root=Path("/sys/fs/cgroup")):
    data = snapshot(root)
    return {k: data[k] for k in ("effectiveCpus", "memoryLimitBytes", "affinityCpus")}


def budget_problem(data, workers):
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
    if not 1 <= workers <= min(4, max(1, int(cpu // 2))):
        return "Requested recognition workers exceed the CPU budget (maximum four)"
    if memory < (384 + workers * 192) * MIB:
        return "The memory budget is too small for the requested recognition workers"
    return pressure_problem(data)


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
