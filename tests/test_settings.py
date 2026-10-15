import json

import pytest

from cassini_live.settings import Acknowledgement, EngineSettings, SettingsStore


def test_round_trip_is_atomic(tmp_path):
    store = SettingsStore(tmp_path / "state" / "settings.json")
    assert store.read() == EngineSettings()
    saved = EngineSettings(
        engine="nemotron-en",
        acknowledgements={"nemotron-en": Acknowledgement("2025-10-24", "", 1760000000.0)},
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
        json.dumps({"acknowledgements": {"nemotron-en": {"version": 1}}}),
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
