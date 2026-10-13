"""Exercise the native CPU-count compatibility bridge in a real subprocess."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def cpu_shim(tmp_path):
    if sys.platform != "linux" or not shutil.which("cc"):
        pytest.skip("Linux C compiler required for CPU shim checks")
    output = tmp_path / "cpu_limit.so"
    source = Path(__file__).parents[1] / "native/cpu_limit.c"
    subprocess.run(["cc", "-shared", "-fPIC", "-O2", "-Wall", "-Wextra", "-Werror",
                    "-o", str(output), str(source), "-ldl", "-pthread"], check=True)
    return output


def test_cpu_bridge_limits_only_cpu_count(cpu_shim):
    env = {**os.environ, "LD_PRELOAD": str(cpu_shim), "CASSINI_WHISTLE_THREADS": "2"}
    script = ('import json,os; print(json.dumps([os.sysconf("SC_NPROCESSORS_ONLN"),'
              'os.sysconf("SC_NPROCESSORS_CONF"),os.sysconf("SC_PAGESIZE")]))')
    result = json.loads(subprocess.check_output([sys.executable, "-c", script], env=env))
    assert 1 <= result[0] <= 2 and 1 <= result[1] <= 2
    assert result[2] == os.sysconf("SC_PAGESIZE")


@pytest.mark.integration
async def test_actual_whistle_worker_has_bounded_native_threads(cpu_shim, monkeypatch):
    from cassini_live.pool import Worker
    models = Path(__file__).parents[1] / "models"
    if not (models / "whistle.cact").exists():
        pytest.skip("Local model required")
    monkeypatch.setenv("CASSINI_WHISTLE_CPU_SHIM", str(cpu_shim))
    monkeypatch.setenv("CASSINI_WHISTLE_THREADS", "2")
    monkeypatch.setenv("NEEDLE3_LIB_PATH", str(models / "libneedle3.so"))
    worker = Worker(str(models / "whistle.cact"))
    try:
        await worker.start()
        assert len(list(Path(f"/proc/{worker.process.pid}/task").iterdir())) <= 2
    finally:
        await worker.close()
