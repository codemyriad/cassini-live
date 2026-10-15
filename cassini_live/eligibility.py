"""Whether this host can run an engine, with a reason an administrator can act on.

Every threshold comes from the catalog, so the settings form and the runtime never
disagree. Whistle is never disabled: it is what the server falls back to.
"""

import asyncio
import math
import platform
import shutil
import time
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from . import __version__, catalog, resources
from .model_store import ModelState

ReasonCode = Literal["limits_missing", "memory_limit", "host_memory", "cpu_quota",
                     "cpu_instructions", "disk_space", "network", "storage", "architecture",
                     "budget"]
NetworkStatus = Literal["reachable", "unreachable", "unknown"]
ARCHITECTURES = ("x86_64", "aarch64")
GIB = 1024**3
MIB = catalog.MIB


@dataclass(frozen=True)
class Reason:
    code: ReasonCode
    message: str
    measured: float | int | str | None = None
    required: float | int | str | None = None


@dataclass(frozen=True)
class Eligibility:
    engine: str
    eligible: bool
    reasons: tuple[Reason, ...]
    advisory: bool = False  # reasons shown but selection allowed (Whistle only)


@dataclass(frozen=True)
class HostFacts:
    cpu_quota: float | None
    affinity_cpus: int
    effective_cpus: float
    memory_limit_bytes: int | None
    swap_limit_bytes: int | None
    host_memory_bytes: int | None
    free_disk_bytes: int | None  # on the model store root; None if storage is unavailable
    arch: str
    cpu_flags: frozenset[str]  # empty when /proc/cpuinfo is unreadable
    network: dict[str, NetworkStatus]
    snapshot: dict | None = None  # the resources.snapshot() the facts came from


_flags: dict[Path, frozenset[str]] = {}


def cpu_flags(cpuinfo=Path("/proc/cpuinfo")) -> frozenset[str]:
    if cpuinfo not in _flags:
        flags: frozenset[str] = frozenset()
        try:
            for line in cpuinfo.read_text().splitlines():
                key, _, value = line.partition(":")
                if key.strip() in ("flags", "Features"):
                    flags = frozenset(value.split())
                    break
        except OSError:
            pass
        _flags[cpuinfo] = flags
    return _flags[cpuinfo]


def free_disk(root: Path | None, disk_usage=shutil.disk_usage) -> int | None:
    """Free bytes where the models would go; the directory may not exist yet."""
    if root is None:
        return None
    for path in (Path(root), *Path(root).parents):
        if path.exists():
            try:
                return disk_usage(path).free
            except OSError:
                return None
    return None


def host_facts(*, reader=resources.snapshot, store_root: Path | None,
               probe: "NetworkProbe | None" = None, cpuinfo=Path("/proc/cpuinfo"),
               disk_usage=shutil.disk_usage, arch: str | None = None) -> HostFacts:
    data = reader()
    return HostFacts(
        cpu_quota=data.get("cpuQuotaCpus"),
        affinity_cpus=data.get("affinityCpus") or 1,
        effective_cpus=data.get("effectiveCpus") or 1.0,
        memory_limit_bytes=data.get("memoryLimitBytes"),
        swap_limit_bytes=data.get("swapLimitBytes"),
        host_memory_bytes=data.get("hostMemoryBytes"),
        free_disk_bytes=free_disk(store_root, disk_usage),
        arch=arch or platform.machine(),
        cpu_flags=cpu_flags(cpuinfo),
        network={
            engine_id: probe.status(engine_id) if probe else "unknown"
            for engine_id, engine in catalog.ENGINES.items()
            if engine.asset is not None
        },
        snapshot=data,
    )


def _gib(value: float, up: bool = False) -> str:
    """One decimal; a requirement rounds up so that the shown value is always enough."""
    return f"{math.ceil(value / GIB * 10 - 1e-9) / 10:.1f}" if up else f"{value / GIB:.1f}"


def _cpus(value: float) -> str:
    return f"{value:g} CPU" + ("" if value == 1 else "s")


def assess(engine: catalog.Engine, facts: HostFacts, model: ModelState) -> Eligibility:
    if engine.kind == "worker_pool":
        return _assess_whistle(engine, facts)
    needs = engine.requirements
    floor = needs.min_memory_mib * MIB
    reasons = []

    def fail(code, message, measured=None, required=None):
        reasons.append(Reason(code, message, measured, required))

    quota, memory, host = facts.cpu_quota, facts.memory_limit_bytes, facts.host_memory_bytes
    if not quota or not memory or facts.swap_limit_bytes != 0:
        fail("limits_missing",
             "Set CPU and memory limits and disable swap for the container first.")
    if memory and memory < floor:
        fail("memory_limit",
             f"The container memory limit is {_gib(memory)} GiB. {engine.name} needs at least "
             f"{_gib(floor, up=True)} GiB "
             f"({_gib(needs.recommended_memory_mib * MIB)} GiB recommended).",
             memory, floor)
    host_needed = 2 * max(memory or 0, floor)
    if not host or host < host_needed:
        fail("host_memory",
             (f"The host has {_gib(host)} GiB of RAM. " if host else
              "The host memory cannot be read. ")
             + f"{engine.name} needs at least {_gib(host_needed, up=True)} GiB, twice the "
             "container limit.", host, host_needed)
    if quota and quota < needs.min_cpu_quota:
        fail("cpu_quota",
             f"The CPU quota is {_cpus(quota)}. {engine.name} needs at least "
             f"{_cpus(needs.min_cpu_quota)}.", quota, needs.min_cpu_quota)
    if facts.arch not in ARCHITECTURES:
        fail("architecture", "This processor architecture is not supported.", facts.arch)
    elif facts.arch == "x86_64" and facts.cpu_flags:
        missing = [flag for flag in needs.cpu_flags if flag not in facts.cpu_flags]
        if missing:
            fail("cpu_instructions",
                 f"This CPU lacks {', '.join(f.upper() for f in missing)}, so {engine.name} "
                 "would not keep up in real time.", None, ",".join(needs.cpu_flags))
    if model.state not in ("ready", "bundled"):
        need = catalog.asset_disk_bytes(engine.asset, model.partial_bytes)
        if facts.free_disk_bytes is None:
            fail("storage",
                 "Persistent app storage is unavailable, so the model cannot be downloaded.")
        elif facts.free_disk_bytes < need:
            fail("disk_space",
                 f"Downloading needs {_gib(need)} GiB of free space; "
                 f"{_gib(facts.free_disk_bytes)} GiB is free.", facts.free_disk_bytes, need)
        if facts.network.get(engine.id) == "unreachable":
            fail("network", "Cannot reach github.com to download the model.")
    return Eligibility(engine.id, not reasons, tuple(reasons))


def _assess_whistle(engine: catalog.Engine, facts: HostFacts) -> Eligibility:
    data = facts.snapshot
    if data is None:
        return Eligibility(engine.id, True, ())
    workers = catalog.worker_ceiling(engine.id, facts.effective_cpus)
    problem = resources.budget_problem(data, workers, engine.id)
    if problem is None:
        return Eligibility(engine.id, True, ())
    return Eligibility(engine.id, True, (Reason("budget", problem + "."),), advisory=True)


class NetworkProbe:
    """HEAD the asset URL (redirects followed), 5 s timeout, results cached for 10 minutes."""

    def __init__(self, opener=urllib.request.urlopen, clock=time.monotonic, ttl=600, *,
                 engines=catalog.ENGINES, timeout=5.0):
        self.opener, self.clock, self.ttl = opener, clock, ttl
        self.engines, self.timeout = engines, timeout
        self._results: dict[str, tuple[float, NetworkStatus]] = {}
        self._lock = asyncio.Lock()

    def status(self, engine_id: str) -> NetworkStatus:
        checked = self._results.get(engine_id)
        if checked is None or self.clock() - checked[0] > self.ttl:
            return "unknown"
        return checked[1]

    async def refresh(self, engine_ids: Iterable[str]) -> None:
        async with self._lock:
            for engine_id in engine_ids:
                engine = self.engines.get(engine_id)
                if engine is None or engine.asset is None or self.status(engine_id) != "unknown":
                    continue
                result = await asyncio.to_thread(self._probe, engine.asset.url)
                self._results[engine_id] = (self.clock(), result)

    def _probe(self, url: str) -> NetworkStatus:
        request = urllib.request.Request(
            url, method="HEAD", headers={"User-Agent": f"cassini-live/{__version__}"}
        )
        try:
            with self.opener(request, timeout=self.timeout):
                return "reachable"
        except (urllib.error.URLError, OSError, ValueError):
            return "unreachable"
