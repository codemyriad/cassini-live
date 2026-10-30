import hashlib
import os
import re
from pathlib import Path

import pytest

from cassini_live import catalog, config

ROOT = Path(__file__).resolve().parent.parent
NEMOTRON = ("nemotron-en", "nemotron-multilingual")


@pytest.mark.parametrize("workers", [1, 2, 3, 4])
@pytest.mark.parametrize("cpus", [0.5, 1, 2, 3, 4, 7.5, 8, 40])
def test_whistle_formulas_equal_the_previous_literals(workers, cpus):
    assert catalog.memory_floor_mib("whistle", workers) == 384 + 192 * workers
    assert catalog.worker_ceiling("whistle", cpus) == min(4, max(1, int(cpus // 2)))
    assert catalog.download_disk_bytes("whistle") == 0


@pytest.mark.parametrize("engine_id", NEMOTRON)
def test_shared_host_floor_and_ceiling(engine_id):
    # Two seats per recognizer thread (Decision 2); threads leave one CPU of the quota free.
    assert [catalog.worker_ceiling(engine_id, c) for c in (1, 2, 3, 4, 8)] == [2, 2, 4, 4, 4]
    assert catalog.worker_ceiling(engine_id, 0.5) == 2
    assert catalog.memory_floor_mib(engine_id, 1) == catalog.memory_floor_mib(engine_id, 4) == 2304
    asset = catalog.get(engine_id).asset
    assert catalog.download_disk_bytes(engine_id, 1000) == (
        asset.download_bytes - 1000 + asset.extracted_bytes + 256 * catalog.MIB
    )


def test_engine_threads_leave_one_cpu_of_the_quota_free():
    quotas = (0.5, 1, 1.5, 2, 3, 4, 8)
    assert [catalog.engine_threads(c) for c in quotas] == [1, 1, 1, 1, 2, 3, 4]


def test_shared_host_floor_matches_its_derivation():
    r = catalog.get("nemotron-en").requirements
    streams = r.stream_mib * catalog.MAX_STREAMS + r.batch_mib * r.max_batch
    steady = r.base_mib + r.model_mib + streams
    assert r.min_memory_mib == -(-int(steady / 0.7) // 256) * 256


def test_engines_are_consistent():
    assert catalog.DEFAULT_ENGINE in catalog.ENGINES
    assert list(catalog.ENGINES) == ["whistle", "nemotron-en", "nemotron-multilingual"]
    for engine_id, engine in catalog.ENGINES.items():
        assert engine.id == engine_id and catalog.get(engine_id) is engine
        assert set(engine.languages) <= set(config.LANGUAGE_NAMES)
        # Memory is measured; CPU stays provisional until a Cassini capacity run.
        assert engine.requirements.provisional or engine_id == "whistle"
    assert catalog.get("nemotron-en").languages == ("en",)
    assert not catalog.get("nemotron-en").automatic_language
    with pytest.raises(KeyError):
        catalog.get("nope")


@pytest.mark.parametrize("engine_id", NEMOTRON)
def test_model_pins(engine_id):
    asset = catalog.get(engine_id).asset
    assert asset.url == asset.files[0].url
    assert 0 < asset.download_bytes < asset.extracted_bytes
    names = [f.name for f in asset.files]
    assert names == ["encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt"]
    for f in asset.files:
        assert re.fullmatch(r"[0-9a-f]{64}", f.sha256) and f.size > 0
        assert re.fullmatch(r"[0-9a-f]{64}", f.zst_sha256) and 0 < f.zst_bytes < f.size
        assert f.url == f"https://dist.gocassini.com/models/files/{f.zst_sha256}/{f.name}.zst"
    assert asset.extracted_bytes == sum(f.size for f in asset.files)
    assert catalog.get("whistle").asset is None


def test_license_files_and_exact_notices():
    for engine in catalog.ENGINES.values():
        assert (ROOT / engine.license.text_file).is_file()
        assert engine.license.summary
    nvidia = catalog.get("nemotron-en").license
    exact = "Licensed by NVIDIA Corporation under the NVIDIA Open Model License"
    assert nvidia.notice == exact
    # Licenses are shown, never accepted.
    assert not any(e.license.requires_acknowledgement for e in catalog.ENGINES.values())
    assert (ROOT / "LICENSES/NVIDIA-Open-Model-License-NOTICE.txt").read_text() == exact + "\n"
    assert "Last Modified: October 24, 2025" in (ROOT / nvidia.text_file).read_text()
    openmdw = catalog.get("nemotron-multilingual").license
    assert not openmdw.requires_acknowledgement
    assert "OpenMDW-1.1" in openmdw.notice and "nvidia/nemotron-3.5" in openmdw.notice
    assert "version 1.1" in (ROOT / openmdw.text_file).read_text()
    assert "Version 2.1" in (ROOT / "LICENSES/LGPL-2.1.txt").read_text()


REFERENCE = ROOT / "local/stt-eval/models"
# The unpacked sherpa-onnx release archives the dist objects were made from.
REFERENCE_DIRS = {
    "nemotron-en": "sherpa-onnx-nemotron-speech-streaming-en-0.6b-560ms-int8-2026-04-25",
    "nemotron-multilingual": "sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11",
}


@pytest.mark.integration
@pytest.mark.parametrize("engine_id", NEMOTRON)
def test_reference_copies_match_the_pins(engine_id):
    """Reads about 650 MB per model; loads nothing, so it needs no memory cap."""
    asset = catalog.get(engine_id).asset
    root = Path(os.environ.get("CASSINI_REFERENCE_MODELS", REFERENCE))
    directory = root / REFERENCE_DIRS[engine_id]
    if not directory.is_dir():
        pytest.skip(f"{directory} is absent")
    for f in asset.files:
        digest = hashlib.sha256()
        with (directory / f.name).open("rb") as source:
            while block := source.read(1 << 20):
                digest.update(block)
        assert (digest.hexdigest(), (directory / f.name).stat().st_size) == (f.sha256, f.size)
