"""The entrypoint hands the persistent volume to the unprivileged account."""

import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("entrypoint", ROOT / "scripts" / "entrypoint.py")
entrypoint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entrypoint)


def test_storage_is_given_to_the_account_without_following_links(tmp_path, monkeypatch):
    storage = tmp_path / "data"
    (storage / "models" / "whistle").mkdir(parents=True)
    (storage / "models" / "whistle" / "weights.bin").write_bytes(b"x")
    (storage / "cassini-live-talk.json").write_text("{}")
    outside = tmp_path / "outside"
    outside.write_text("not ours")
    (storage / "link").symlink_to(outside)
    changed = []
    monkeypatch.setattr(entrypoint.os, "lchown", lambda p, u, g: changed.append((p, u, g)))
    entrypoint.own_storage(storage, 10001, 10001)
    paths = {Path(p).relative_to(storage).as_posix() for p, _, _ in changed}
    assert paths == {
        ".", "models", "models/whistle", "models/whistle/weights.bin",
        "cassini-live-talk.json", "link",  # the link itself, never its target
    }
    assert {(u, g) for _, u, g in changed} == {(10001, 10001)}
    assert (storage.stat().st_mode & 0o777) == 0o700
    assert outside.read_text() == "not ours"


def test_entries_already_owned_are_left_alone(tmp_path, monkeypatch):
    storage = tmp_path / "data"
    storage.mkdir()
    changed = []
    monkeypatch.setattr(entrypoint.os, "lchown", lambda p, u, g: changed.append(p))
    entrypoint.own_storage(storage, os.getuid(), os.getgid())
    assert changed == []


def test_a_missing_volume_is_skipped(tmp_path):
    entrypoint.own_storage(tmp_path / "absent", 10001, 10001)
