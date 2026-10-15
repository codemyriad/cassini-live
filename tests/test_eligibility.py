import asyncio
import dataclasses
import urllib.error

import pytest

from cassini_live import catalog, eligibility
from cassini_live.eligibility import HostFacts, NetworkProbe, assess, host_facts
from cassini_live.model_store import ModelState

GIB = 1024**3
ABSENT = ModelState("nemotron-en", "absent", None)
READY = ModelState("nemotron-en", "ready", None)
GOOD = HostFacts(
    cpu_quota=4.0,
    affinity_cpus=8,
    effective_cpus=4.0,
    memory_limit_bytes=3 * GIB,
    swap_limit_bytes=0,
    host_memory_bytes=16 * GIB,
    free_disk_bytes=20 * GIB,
    arch="x86_64",
    cpu_flags=frozenset({"sse4_2", "avx", "avx2"}),
    network={"nemotron-en": "reachable", "nemotron-multilingual": "unknown"},
)
CASES = {
    "limits_missing": dict(swap_limit_bytes=None),
    "memory_limit": dict(memory_limit_bytes=2 * GIB),
    "host_memory": dict(host_memory_bytes=5 * GIB),
    "cpu_quota": dict(cpu_quota=0.5),
    "cpu_instructions": dict(cpu_flags=frozenset({"sse4_2", "avx"})),
    "architecture": dict(arch="riscv64"),
    "storage": dict(free_disk_bytes=None),
    "disk_space": dict(free_disk_bytes=GIB),
    "network": dict(network={"nemotron-en": "unreachable"}),
}


def codes(result):
    return [reason.code for reason in result.reasons]


@pytest.mark.parametrize("engine_id", ["nemotron-en", "nemotron-multilingual"])
def test_a_good_host_is_eligible(engine_id):
    result = assess(catalog.get(engine_id), GOOD, ABSENT)
    assert result.eligible and result.reasons == () and not result.advisory


@pytest.mark.parametrize("code", sorted(CASES))
def test_each_failing_rule_disables_with_a_reason(code):
    result = assess(catalog.get("nemotron-en"), dataclasses.replace(GOOD, **CASES[code]), ABSENT)
    assert not result.eligible
    assert code in codes(result)
    reason = result.reasons[codes(result).index(code)]
    assert reason.message.endswith(".") and len(reason.message) < 200


def test_messages_carry_numbers_from_the_catalog():
    facts = dataclasses.replace(
        GOOD,
        memory_limit_bytes=2 * GIB,
        host_memory_bytes=4 * GIB,
        cpu_quota=0.5,
        free_disk_bytes=GIB // 2,
    )
    reasons = {r.code: r for r in assess(catalog.get("nemotron-en"), facts, ABSENT).reasons}
    assert reasons["memory_limit"].message == (
        "The container memory limit is 2.0 GiB. Nemotron English needs at least 2.3 GiB "
        "(3.0 GiB recommended)."
    )
    assert reasons["host_memory"].message == (
        "The host has 4.0 GiB of RAM. Nemotron English needs at least 4.5 GiB, twice the "
        "container limit."
    )
    assert reasons["cpu_quota"].message == (
        "The CPU quota is 0.5 CPUs. Nemotron English needs at least 1 CPU."
    )
    assert reasons["disk_space"].required == catalog.download_disk_bytes("nemotron-en")
    assert "0.5 GiB is free" in reasons["disk_space"].message


def test_every_failing_rule_is_reported():
    facts = dataclasses.replace(
        GOOD,
        **{
            k: v
            for case in CASES.values()
            for k, v in case.items()
            if k not in ("arch", "free_disk_bytes")
        }
        | {"host_memory_bytes": 4 * GIB},
    )
    assert set(codes(assess(catalog.get("nemotron-en"), facts, ABSENT))) == {
        "limits_missing",
        "memory_limit",
        "host_memory",
        "cpu_quota",
        "cpu_instructions",
        "network",
    }


def test_host_memory_follows_a_larger_container_limit():
    facts = dataclasses.replace(GOOD, memory_limit_bytes=4 * GIB, host_memory_bytes=7 * GIB)
    assert codes(assess(catalog.get("nemotron-en"), facts, ABSENT)) == ["host_memory"]


def test_unknown_network_does_not_disable():
    facts = dataclasses.replace(GOOD, network={})
    assert assess(catalog.get("nemotron-multilingual"), GOOD, ABSENT).eligible
    assert assess(catalog.get("nemotron-en"), facts, ABSENT).eligible


@pytest.mark.parametrize("code", ["storage", "disk_space", "network"])
def test_download_rules_are_ignored_when_the_model_is_ready(code):
    facts = dataclasses.replace(GOOD, **CASES[code])
    assert assess(catalog.get("nemotron-en"), facts, READY).eligible


def test_partial_download_lowers_the_disk_need():
    need = catalog.download_disk_bytes("nemotron-en")
    facts = dataclasses.replace(GOOD, free_disk_bytes=need - 1000)
    assert not assess(catalog.get("nemotron-en"), facts, ABSENT).eligible
    partial = ModelState("nemotron-en", "partial", None, partial_bytes=2000)
    assert assess(catalog.get("nemotron-en"), facts, partial).eligible


def test_no_avx2_requirement_on_aarch64_or_with_unreadable_flags():
    for facts in (
        dataclasses.replace(GOOD, arch="aarch64", cpu_flags=frozenset({"asimd"})),
        dataclasses.replace(GOOD, cpu_flags=frozenset()),
    ):
        assert assess(catalog.get("nemotron-en"), facts, ABSENT).eligible


def snapshot(**overrides):
    data = {
        "monitoringAvailable": True,
        "pressureAvailable": True,
        "cpuPressure": 0,
        "cpuPressureRecent": 0,
        "hostCpuPressure": 0,
        "memoryPressure": 0,
        "effectiveCpus": 4.0,
        "cpuQuotaCpus": 4.0,
        "affinityCpus": 8,
        "memoryLimitBytes": 3 * GIB,
        "memoryCurrentBytes": 300 * 1024**2,
        "memoryFileBytes": 0,
        "memoryStatAvailable": True,
        "cpuStallUsec": 0,
        "hostCpuStallUsec": 0,
        "swapLimitBytes": 0,
        "pidsLimit": 256,
        "memoryFraction": 0.1,
        "pidsFraction": 0.1,
        "hostMemoryBytes": 16 * GIB,
        "hostAvailableBytes": 12 * GIB,
        "throttledPeriods": 0,
        "throttledUsec": 0,
        "cpuPeriods": 0,
        "oomKills": 0,
    }
    return data | overrides


def test_whistle_is_never_disabled_and_carries_advisory_budget_reasons():
    whistle = catalog.get("whistle")
    worst = dataclasses.replace(
        GOOD,
        cpu_quota=None,
        memory_limit_bytes=None,
        arch="riscv64",
        free_disk_bytes=None,
        network={},
        snapshot=snapshot(),
    )
    result = assess(whistle, worst, ModelState("whistle", "bundled", None))
    assert result.eligible and result.reasons == () and not result.advisory
    unsafe = dataclasses.replace(worst, snapshot=snapshot(swapLimitBytes=None))
    result = assess(whistle, unsafe, ModelState("whistle", "bundled", None))
    assert result.eligible and result.advisory
    assert codes(result) == ["budget"]
    assert result.reasons[0].message.startswith("Set finite cgroup v2 CPU, memory and PID")


def test_host_facts_from_a_fake_reader_and_cpuinfo(tmp_path):
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("processor\t: 0\nflags\t\t: fpu sse4_2 avx2\n\nprocessor\t: 1\n")
    store = tmp_path / "state" / "models"
    (tmp_path / "state").mkdir()
    seen = []

    class Disk:
        free = 7 * GIB

    def disk_usage(path):
        seen.append(path)
        return Disk

    class Probe:
        def status(self, engine_id):
            return "unreachable" if engine_id == "nemotron-en" else "reachable"

    facts = host_facts(
        reader=lambda: snapshot(cpuQuotaCpus=2.0),
        store_root=store,
        probe=Probe(),
        cpuinfo=cpuinfo,
        disk_usage=disk_usage,
        arch="x86_64",
    )
    assert facts.cpu_quota == 2.0 and facts.affinity_cpus == 8
    assert facts.memory_limit_bytes == 3 * GIB and facts.swap_limit_bytes == 0
    assert facts.host_memory_bytes == 16 * GIB
    assert facts.free_disk_bytes == 7 * GIB and seen == [tmp_path / "state"]
    assert facts.cpu_flags == {"fpu", "sse4_2", "avx2"}
    assert facts.network == {"nemotron-en": "unreachable", "nemotron-multilingual": "reachable"}
    facts = host_facts(reader=snapshot, store_root=None, cpuinfo=tmp_path / "missing")
    assert facts.free_disk_bytes is None and facts.cpu_flags == frozenset()
    assert set(facts.network.values()) == {"unknown"}


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


def test_network_probe_heads_the_asset_and_caches():
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def opener(request, timeout):
        calls.append((request.get_method(), request.full_url, timeout))
        if request.full_url == catalog.get("nemotron-multilingual").asset.url:
            raise urllib.error.URLError("no route")
        return Response()

    clock = Clock()
    probe = NetworkProbe(opener=opener, clock=clock)
    assert probe.status("nemotron-en") == "unknown"
    asyncio.run(probe.refresh(["whistle", "nemotron-en", "nemotron-multilingual"]))
    assert probe.status("nemotron-en") == "reachable"
    assert probe.status("nemotron-multilingual") == "unreachable"
    assert calls[0] == ("HEAD", catalog.get("nemotron-en").asset.url, 5.0)
    assert len(calls) == 2
    asyncio.run(probe.refresh(["nemotron-en"]))
    assert len(calls) == 2
    clock.now += 601
    assert probe.status("nemotron-en") == "unknown"
    asyncio.run(probe.refresh(["nemotron-en"]))
    assert len(calls) == 3 and probe.status("nemotron-en") == "reachable"


def test_cpu_flags_are_cached(tmp_path):
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("Features\t: fp asimd\n")
    assert eligibility.cpu_flags(cpuinfo) == {"fp", "asimd"}
    cpuinfo.write_text("Features\t: fp\n")
    assert eligibility.cpu_flags(cpuinfo) == {"fp", "asimd"}
