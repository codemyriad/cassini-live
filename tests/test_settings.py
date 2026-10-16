import json

import pytest

from cassini_live.settings import EngineSettings, SettingsStore


def test_round_trip_is_atomic(tmp_path):
    store = SettingsStore(tmp_path / "state" / "settings.json")
    assert store.read() == EngineSettings()
    saved = EngineSettings(
        engine="nemotron-en",
        last_error={"code": "timeout", "message": "Too slow", "engine": "nemotron-en", "at": 1},
        updated_at=5,
    )
    store.write(saved)
    assert store.read() == saved
    assert not list((tmp_path / "state").glob("*.tmp"))


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[]",
        json.dumps({"engine": "vosk"}),
        json.dumps({"engine": "whistle", "pending": "vosk"}),
        json.dumps({"engine": "whistle", "pad": "x" * 5000}),
    ],
)
def test_invalid_files_are_refused(tmp_path, content):
    path = tmp_path / "settings.json"
    path.write_text(content)
    with pytest.raises(ValueError):
        SettingsStore(path).read()


def test_size_cap_on_write(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    with pytest.raises(ValueError):
        store.write(EngineSettings(last_error={"message": "x" * 5000}))


def test_in_memory_mode():
    store = SettingsStore(None)
    store.write(EngineSettings(engine="nemotron-multilingual"))
    assert store.read().engine == "nemotron-multilingual"


@pytest.mark.parametrize(
    "acknowledgements",
    [
        {"nemotron-en": {"version": "2025-10-24", "user": "", "at": 1760000000.0}},
        {"nemotron-en": {"version": 1}},
        {"vosk": "yes"},
        [],
        "accepted",
    ],
)
def test_old_acknowledgements_are_ignored_and_dropped(tmp_path, acknowledgements):
    # 0.7.0 saved license acknowledgements; files with any shape of them keep loading.
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "engine": "nemotron-en", "pending": None, "acknowledgements": acknowledgements,
        "lastError": None, "updatedAt": 3,
    }))
    store = SettingsStore(path)
    settings = store.read()
    assert settings == EngineSettings(engine="nemotron-en", updated_at=3)
    store.write(settings)
    assert "acknowledgements" not in json.loads(path.read_text())
    assert store.read() == settings
