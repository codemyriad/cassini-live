import logging

import pytest

from cassini_live import resources

MIB = resources.MIB
GIB = 1024 * MIB
CGROUP = {
    "cpu.max": "200000 100000",
    "cpu.stat": "nr_periods 10\nnr_throttled 0\nthrottled_usec 0\n",
    "cpu.pressure": "some avg10=0.00 avg60=0.10 avg300=0.05 total=987654\n"
    "full avg10=0.00 avg60=0.00 avg300=0.00 total=12\n",
    "memory.max": str(3 * GIB),
    "memory.current": str(1500 * MIB),
    "memory.stat": "anon 471859200\nfile 1048576000\nkernel 8388608\nshmem 104857600\n",
    "memory.swap.max": "0",
    "memory.events": "oom_kill 0\n",
    "pids.max": "256",
    "pids.current": "20",
    "meminfo": "MemTotal: 16777216 kB\nMemAvailable: 8388608 kB\n",
    "pressure/cpu": "some avg10=1.25 avg60=4.50 avg300=2.00 total=50\n",
}


@pytest.fixture
def cgroup(tmp_path, monkeypatch):
    """A staging-shaped container: 2 CPU, 3 GiB, 1.5 GiB charged of which 1000 MiB is cache
    and 100 MiB of that cache is shared memory."""
    monkeypatch.setattr(resources, "_paths", lambda root: [root])
    monkeypatch.setattr(resources, "_stat_warned", False)
    (tmp_path / "pressure").mkdir()
    for name, value in CGROUP.items():
        (tmp_path / name).write_text(value)
    return tmp_path


def fit_data(limit_mib, current_mib, file_mib=None, shmem_mib=0):
    return {
        "memoryLimitBytes": None if limit_mib is None else limit_mib * MIB,
        "memoryCurrentBytes": current_mib * MIB,
        "memoryFileBytes": None if file_mib is None else file_mib * MIB,
        "memoryShmemBytes": None if file_mib is None else shmem_mib * MIB,
        "memoryStatAvailable": file_mib is not None,
    }


def test_snapshot_reports_charged_memory_page_cache_and_stall_counters(cgroup):
    data = resources.snapshot(cgroup, cgroup)
    assert data["memoryCurrentBytes"] == 1500 * MIB
    assert data["memoryFileBytes"] == 1000 * MIB
    assert data["memoryShmemBytes"] == 100 * MIB
    assert data["memoryStatAvailable"] is True
    assert data["cpuStallUsec"] == 987654
    assert data["hostCpuStallUsec"] == 50
    # Shared memory is in the file figure but stays charged: 1500 - (1000 - 100) used.
    assert resources.headroom_mib(data) == 3072 - 600
    (cgroup / "memory.stat").write_text("anon 1\nfile 1048576000\n")
    assert resources.snapshot(cgroup, cgroup)["memoryShmemBytes"] == 0
    (cgroup / "cpu.pressure").unlink()
    (cgroup / "pressure/cpu").unlink()
    data = resources.snapshot(cgroup, cgroup)
    assert data["cpuStallUsec"] is None and data["hostCpuStallUsec"] is None


def test_pressure_total_parses_only_a_sane_some_counter(tmp_path):
    path = tmp_path / "cpu.pressure"
    path.write_text("some avg10=0.00 avg60=0.00 avg300=0.00 total=1\n")
    assert resources._pressure_total(path) == 1
    path.write_text("full avg10=0.00 avg60=0.00 avg300=0.00 total=1\n")
    assert resources._pressure_total(path) is None
    path.write_text("some avg10=0.00 avg60=0.00 avg300=0.00\n")
    assert resources._pressure_total(path) is None
    path.write_text("some avg10=0.00 avg60=0.00 avg300=0.00 total=-1\n")
    assert resources._pressure_total(path) is None
    path.write_text("some avg10=0.00 avg60=0.00 avg300=0.00 total=1.5\n")
    assert resources._pressure_total(path) is None
    assert resources._pressure_total(tmp_path / "missing") is None


def test_snapshot_counts_page_cache_as_used_without_memory_stat_and_logs_once(cgroup, caplog):
    (cgroup / "memory.stat").unlink()
    with caplog.at_level(logging.WARNING, logger="cassini_live.resources"):
        data = resources.snapshot(cgroup, cgroup)
        resources.snapshot(cgroup, cgroup)
    assert data["memoryCurrentBytes"] == 1500 * MIB
    assert data["memoryFileBytes"] is None and data["memoryShmemBytes"] is None
    assert data["memoryStatAvailable"] is False
    assert data["monitoringAvailable"] is True
    assert resources.headroom_mib(data) == 3072 - 1500
    assert [r.getMessage() for r in caplog.records] == [
        "memory.stat is unreadable; memory headroom counts page cache as used"
    ]
    (cgroup / "memory.stat").write_text("anon 1\n")
    assert resources.snapshot(cgroup, cgroup)["memoryStatAvailable"] is False


def test_snapshot_charges_memory_against_the_binding_cgroup(tmp_path, monkeypatch):
    root, child = tmp_path, tmp_path / "child"
    child.mkdir()
    monkeypatch.setattr(resources, "_paths", lambda _: [root, child])
    (root / "memory.current").write_text(str(2 * GIB))
    (root / "memory.stat").write_text("file 0\n")
    (child / "memory.max").write_text(str(1 * GIB))
    (child / "memory.current").write_text(str(600 * MIB))
    (child / "memory.stat").write_text(f"file {100 * MIB}\n")
    data = resources.snapshot(root, tmp_path)
    assert data["memoryLimitBytes"] == 1 * GIB
    assert data["memoryCurrentBytes"] == 600 * MIB and data["memoryFileBytes"] == 100 * MIB
    assert resources.headroom_mib(data) == 1024 - 500
    (child / "memory.max").write_text("max")
    data = resources.snapshot(root, tmp_path)
    assert data["memoryCurrentBytes"] == 2 * GIB and resources.headroom_mib(data) is None


def test_binding_cgroup_tie_goes_to_the_outer_cgroup(tmp_path, monkeypatch):
    # A scope capped at the same figure as its slice: the slice's charge includes the scope's
    # and everything beside it, so that is what the limit is measured against.
    root, slice_ = tmp_path, tmp_path / "app.slice"
    scope = slice_ / "run.scope"
    scope.mkdir(parents=True)
    monkeypatch.setattr(resources, "_paths", lambda _: [root, scope, slice_])  # innermost first
    (root / "memory.current").write_text(str(2 * GIB))
    for path, current in ((slice_, 800 * MIB), (scope, 600 * MIB)):
        (path / "memory.max").write_text(str(GIB))
        (path / "memory.current").write_text(str(current))
        (path / "memory.stat").write_text("file 0\nshmem 0\n")
    data = resources.snapshot(root, tmp_path)
    assert data["memoryLimitBytes"] == GIB and data["memoryCurrentBytes"] == 800 * MIB
    (scope / "memory.max").write_text(str(GIB - 1))
    data = resources.snapshot(root, tmp_path)
    assert data["memoryLimitBytes"] == GIB - 1 and data["memoryCurrentBytes"] == 600 * MIB


def test_cpu_stall_is_read_from_the_innermost_cgroup_with_the_root_as_fallback(
    tmp_path, monkeypatch
):
    root, child = tmp_path, tmp_path / "child"
    child.mkdir()
    monkeypatch.setattr(resources, "_paths", lambda _: [root, child])
    (root / "cpu.pressure").write_text("some avg10=0.00 avg60=0.00 avg300=0.00 total=5000\n")
    (child / "cpu.pressure").write_text("some avg10=0.00 avg60=0.00 avg300=0.00 total=700\n")
    assert resources.snapshot(root, tmp_path)["cpuStallUsec"] == 700
    (child / "cpu.pressure").unlink()
    assert resources.snapshot(root, tmp_path)["cpuStallUsec"] == 5000


def test_headroom_is_unknown_without_a_limit_or_a_reading():
    assert resources.headroom_mib(fit_data(None, 100)) is None
    assert resources.headroom_mib({"memoryLimitBytes": 3 * GIB}) is None
    assert resources.headroom_mib(fit_data(3072, 3072 + 64, 0)) == -64
    # Shared memory never reclaims, and a stat with more shmem than file charges nothing.
    assert resources.headroom_mib(fit_data(2048, 1500, 1200, shmem_mib=200)) == 2048 - 500
    assert resources.headroom_mib(fit_data(2048, 1500, 100, shmem_mib=300)) == 2048 - 1500


def test_nemotron_start_fit_on_three_and_two_gib_and_unlimited(cgroup):
    # 1024 (model) + 16 * 12 (streams) + 45 * 4 (batch) + 128 (supervisor) MiB
    need = 1024 + 192 + 180 + 128
    assert resources.start_problem(resources.snapshot(cgroup, cgroup), "nemotron-en") is None
    assert resources.start_problem(fit_data(3072, 3072 - need, 0), "nemotron-en") is None
    assert "Not enough memory to load Nemotron English" in resources.start_problem(
        fit_data(3072, 3072 - need + 1, 0), "nemotron-en"
    )
    # 2 GiB idle with Whistle active: fits, so a `docker update --memory 2g` test container
    # loads; the measured RSS replaces the catalog figure once a host has reported one.
    idle = fit_data(2048, 300, 50)
    assert resources.start_problem(idle, "nemotron-en") is None
    assert resources.start_problem(idle, "nemotron-multilingual") is None
    assert "Not enough memory" in resources.start_problem(idle, "nemotron-en", rss_mib=1600)
    assert resources.start_problem(idle, "nemotron-en", rss_mib=900) is None
    # A warm page cache does not count; the same reading without memory.stat does.
    cached = fit_data(2048, 1500, 1200)
    assert resources.start_problem(cached, "nemotron-en") is None
    problem = resources.start_problem(fit_data(2048, 1500), "nemotron-en")
    assert problem == f"Not enough memory to load Nemotron English: {need} MiB needed, 548 MiB free"
    assert resources.start_problem(fit_data(None, 300, 50), "nemotron-en") == (
        "Set finite cgroup v2 memory limits"
    )
    assert "cannot be read" in resources.start_problem(
        {"memoryLimitBytes": 3 * GIB, "memoryCurrentBytes": None}, "nemotron-en"
    )


def test_whistle_start_fit_needs_192_mib_per_worker_still_to_start():
    assert resources.start_problem(fit_data(576, 384, 0), "whistle") is None
    assert resources.start_problem(fit_data(576, 384, 0), "whistle", workers=1) is None
    assert "Not enough memory to load Whistle" in resources.start_problem(
        fit_data(576, 385, 0), "whistle"
    )
    assert resources.start_problem(fit_data(2048, 300, 100), "whistle", workers=4) is None
    assert resources.start_problem(fit_data(768, 300, 0), "whistle", workers=2) is None
    assert resources.start_problem(fit_data(768, 300, 0), "whistle", workers=3)
    assert resources.start_problem(fit_data(None, 300, 0), "whistle") == (
        "Set finite cgroup v2 memory limits"
    )


def test_reads_enforced_budget_and_refuses_missing_counters(tmp_path, monkeypatch):
    monkeypatch.setattr(resources, "_paths", lambda root: [root])
    monkeypatch.setattr(resources.os, "sched_getaffinity", lambda _: set(range(8)))
    files = {
        "cpu.max": "400000 100000",
        "cpu.stat": "nr_periods 10\nnr_throttled 2\nthrottled_usec 321000\n",
        "memory.max": str(2048 * resources.MIB),
        "memory.current": str(200 * resources.MIB),
        "memory.swap.max": "0",
        "memory.events": "oom_kill 0\n",
        "pids.max": "128",
        "pids.current": "20",
        "meminfo": "MemTotal: 8388608 kB\nMemAvailable: 4194304 kB\n",
    }
    for name, value in files.items():
        (tmp_path / name).write_text(value)
    (tmp_path / "pressure").mkdir()
    (tmp_path / "pressure/cpu").write_text("some avg10=1.25 avg60=4.50 avg300=2.00 total=50\n")
    data = resources.snapshot(tmp_path, tmp_path)
    assert resources.static_problem(data, 2) is None
    assert data["cpuQuotaCpus"] == 4 and data["swapLimitBytes"] == 0
    assert data["throttledPeriods"] == 2 and data["cpuPeriods"] == 10
    assert data["throttledUsec"] == 321000
    assert data["hostCpuPressure"] == 1.25
    (tmp_path / "pressure/cpu").unlink()
    assert resources.snapshot(tmp_path, tmp_path)["hostCpuPressure"] is None
    (tmp_path / "memory.current").unlink()
    unreadable = resources.snapshot(tmp_path, tmp_path)
    assert not unreadable["monitoringAvailable"]
    assert "cannot be read" in resources.start_problem(unreadable, "whistle")


def test_static_rules_block_and_half_host_rules_only_advise():
    from tests.test_safety import SAFE

    MIB = resources.MIB
    for workers in (1, 2, 3):
        assert resources.static_problem(SAFE, workers) == resources.static_problem(
            SAFE, workers, "whistle"
        )
    small = {**SAFE, "memoryLimitBytes": 2048 * MIB}
    assert "2.3 GiB" in resources.static_problem(small, 1, "nemotron-en")
    roomy = {**SAFE, "memoryLimitBytes": 3072 * MIB}
    assert resources.static_problem(roomy, 4, "nemotron-en") is None
    assert "CPU budget" in resources.static_problem(roomy, 5, "nemotron-multilingual")
    one_cpu = {**roomy, "cpuQuotaCpus": 0.5, "effectiveCpus": 0.5}
    assert resources.static_problem(one_cpu, 1, "nemotron-en")
    # Decision 5: these were blockers in 0.7.x and are advice now.
    greedy = {**SAFE, "cpuQuotaCpus": 8, "memoryLimitBytes": 8192 * MIB, "pidsLimit": 500}
    assert resources.static_problem(greedy, 1) is None
    assert len(resources.advice(greedy)) == 3
    assert resources.advice(SAFE) == []
