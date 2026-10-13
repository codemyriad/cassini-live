from cassini_live import resources


def test_reads_enforced_budget_and_refuses_missing_counters(tmp_path, monkeypatch):
    monkeypatch.setattr(resources, "_paths", lambda root: [root])
    monkeypatch.setattr(resources.os, "sched_getaffinity", lambda _: set(range(8)))
    files = {
        "cpu.max": "400000 100000",
        "cpu.stat": "nr_periods 10\nnr_throttled 2\n",
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
    data = resources.snapshot(tmp_path, tmp_path)
    assert resources.budget_problem(data, 2) is None
    assert data["cpuQuotaCpus"] == 4 and data["swapLimitBytes"] == 0
    assert data["throttledPeriods"] == 2 and data["cpuPeriods"] == 10
    (tmp_path / "memory.current").unlink()
    assert "cannot be read" in resources.budget_problem(resources.snapshot(tmp_path, tmp_path), 2)
