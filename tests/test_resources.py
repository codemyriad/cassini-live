from cassini_live import resources


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
    assert resources.budget_problem(data, 2) is None
    assert data["cpuQuotaCpus"] == 4 and data["swapLimitBytes"] == 0
    assert data["throttledPeriods"] == 2 and data["cpuPeriods"] == 10
    assert data["throttledUsec"] == 321000
    assert data["hostCpuPressure"] == 1.25
    (tmp_path / "pressure/cpu").unlink()
    assert resources.snapshot(tmp_path, tmp_path)["hostCpuPressure"] is None
    (tmp_path / "memory.current").unlink()
    assert "cannot be read" in resources.budget_problem(resources.snapshot(tmp_path, tmp_path), 2)


def test_whistle_budget_is_unchanged_and_nemotron_needs_its_floor():
    from tests.test_safety import SAFE

    MIB = resources.MIB
    for workers in (1, 2, 3):
        assert resources.budget_problem(SAFE, workers) == resources.budget_problem(
            SAFE, workers, "whistle"
        )
    small = {**SAFE, "memoryLimitBytes": 2048 * MIB}
    assert "2.3 GiB" in resources.budget_problem(small, 1, "nemotron-en")
    roomy = {**SAFE, "memoryLimitBytes": 3072 * MIB}
    assert resources.budget_problem(roomy, 4, "nemotron-en") is None
    assert "CPU budget" in resources.budget_problem(roomy, 5, "nemotron-multilingual")
    one_cpu = {**roomy, "cpuQuotaCpus": 0.5, "effectiveCpus": 0.5}
    assert resources.budget_problem(one_cpu, 1, "nemotron-en")


def test_load_problem_keeps_the_model_below_the_trip():
    from tests.test_safety import SAFE

    MIB = resources.MIB
    data = {**SAFE, "memoryLimitBytes": 3072 * MIB, "memoryFraction": 0.2}
    assert resources.load_problem(data, "whistle") is None
    assert resources.load_problem(data, "nemotron-en") is None
    assert "Not enough free" in resources.load_problem(
        {**data, "memoryFraction": 0.5}, "nemotron-en"
    )
    assert resources.load_problem({**data, "memoryFraction": 0.9}, "nemotron-en")
