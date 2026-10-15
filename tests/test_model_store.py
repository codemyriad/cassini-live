import asyncio
import json
import tarfile
import threading

import pytest

from cassini_live import model_store
from cassini_live.model_store import ModelError, ModelStore
from tests.fakes_models import (
    DIRECTORY,
    FILES,
    Disk,
    FakeOpener,
    fake_catalog,
    make_archive,
    member,
)

ENGINE = "nemotron-en"


def store_for(tmp_path, archive, opener=None, engines=None, disk=None, **opener_args):
    opener = opener or FakeOpener(archive, **opener_args)
    store = ModelStore(tmp_path / "models", engines=engines or fake_catalog(archive),
                       opener=opener, disk_usage=disk or Disk(), clock=lambda: 1760000000)
    return store, opener


def partial_paths(store):
    partial = store.root / ".partial" / f"{ENGINE}.tar.bz2"
    return partial, partial.with_name(partial.name + ".json")


async def failure(store, code):
    with pytest.raises(ModelError) as error:
        await store.download(ENGINE)
    assert error.value.code == code, str(error.value)
    return error.value


def test_happy_path_installs_atomically_with_license_and_manifest(tmp_path):
    archive = make_archive()
    samples = []

    def sample(_response):
        progress = store.progress(ENGINE)
        samples.append((progress.phase, progress.bytes_done))

    store, opener = store_for(tmp_path, archive, on_read=sample)
    assert store.state(ENGINE).state == "absent"
    final = asyncio.run(store.download(ENGINE))

    assert final == store.model_dir(ENGINE) == store.root / ENGINE
    for name, data in FILES.items():
        assert (final / name).read_bytes() == data
    assert not (final / "test_wavs").exists()
    assert (final / "NOTICE.txt").read_text() == (
        "Licensed by NVIDIA Corporation under the NVIDIA Open Model License\n"
    )
    assert "NVIDIA Open Model License Agreement" in (final / "LICENSE.txt").read_text()
    manifest = json.loads((final / "manifest.json").read_text())
    assert manifest["archiveBytes"] == len(archive)
    assert manifest["licenseVersion"] == "2025-10-24"
    assert manifest["installedAt"] == 1760000000
    assert len(manifest["archiveSha256Observed"]) == 64
    assert not any(p.exists() for p in partial_paths(store))
    assert sorted(p.name for p in store.root.iterdir()) == [".partial", ENGINE]
    assert samples and all(phase == "downloading" for phase, _ in samples)
    assert [done for _, done in samples] == sorted(done for _, done in samples)
    assert opener.requests[0].get_header("User-agent").startswith("cassini-live/")
    assert store.state(ENGINE).state == "ready" and store.busy is None
    assert store.progress(ENGINE) is None


def test_content_length_mismatch_is_refused_before_writing(tmp_path):
    archive = make_archive()
    store, _ = store_for(tmp_path, archive, content_length=len(archive) + 1)
    error = asyncio.run(failure(store, "size_mismatch"))
    assert "upstream model archive changed" in str(error)
    assert not partial_paths(store)[0].exists()


def test_body_longer_than_pinned_size_is_never_written_past_it(tmp_path):
    archive = make_archive()
    engines = fake_catalog(archive, archive_bytes=len(archive) - 10)
    store, _ = store_for(tmp_path, archive, engines=engines, content_length=len(archive) - 10)
    asyncio.run(failure(store, "size_mismatch"))
    assert partial_paths(store)[0].stat().st_size == len(archive) - 10


def write_partial(store, data, etag='"v1"', archive_bytes=None):
    partial, sidecar = partial_paths(store)
    partial.parent.mkdir(parents=True)
    partial.write_bytes(data)
    sidecar.write_text(json.dumps({"url": "x", "etag": etag, "lastModified": None,
                                   "archiveBytes": archive_bytes}))


def test_resume_with_206_appends(tmp_path):
    archive = make_archive()
    store, opener = store_for(tmp_path, archive)
    half = len(archive) // 2
    write_partial(store, archive[:half], archive_bytes=len(archive))
    assert store.state(ENGINE).state == "partial"
    assert store.state(ENGINE).partial_bytes == half
    asyncio.run(store.download(ENGINE))
    request = opener.requests[0]
    assert request.get_header("Range") == f"bytes={half}-"
    assert request.get_header("If-range") == '"v1"'
    assert store.state(ENGINE).state == "ready"


@pytest.mark.parametrize("args", [{"ignore_range": True}, {"etag": '"v2"'}])
def test_server_ignoring_range_or_changed_validator_restarts(tmp_path, args):
    archive = make_archive()
    store, opener = store_for(tmp_path, archive, **args)
    write_partial(store, b"garbage" * 20, archive_bytes=len(archive))
    asyncio.run(store.download(ENGINE))
    assert opener.requests[0].get_header("Range") == "bytes=140-"
    assert store.state(ENGINE).state == "ready"


def test_partial_without_validator_starts_over(tmp_path):
    archive = make_archive()
    store, opener = store_for(tmp_path, archive)
    write_partial(store, b"x" * 50, etag=None, archive_bytes=len(archive))
    asyncio.run(store.download(ENGINE))
    assert opener.requests[0].get_header("Range") is None


def test_interrupted_download_keeps_partial_and_resumes(tmp_path):
    archive = make_archive()
    store, opener = store_for(tmp_path, archive, stall=100)
    asyncio.run(failure(store, "network"))
    assert partial_paths(store)[0].stat().st_size == 100
    assert store.state(ENGINE).state == "partial"
    assert store.state(ENGINE).error
    opener.stall = None
    asyncio.run(store.download(ENGINE))
    assert opener.requests[1].get_header("Range") == "bytes=100-"
    assert store.state(ENGINE).state == "ready"


def test_http_error_is_network(tmp_path):
    store, _ = store_for(tmp_path, make_archive(), status=404)
    error = asyncio.run(failure(store, "network"))
    assert "404" in str(error)
    assert store.state(ENGINE).state == "error"


@pytest.mark.parametrize("discard", [False, True])
def test_cancel_keeps_partial_unless_discarded(tmp_path, discard):
    archive = make_archive()

    def cancel(response):
        if response.position >= 100:
            assert store.cancel(ENGINE, discard=discard)

    store, _ = store_for(tmp_path, archive, on_read=cancel)
    asyncio.run(failure(store, "cancelled"))
    assert partial_paths(store)[0].exists() is not discard
    assert store.busy is None
    assert store.state(ENGINE).error is None


def test_cancel_when_idle_with_discard_removes_partial(tmp_path):
    store, _ = store_for(tmp_path, make_archive())
    write_partial(store, b"x" * 10)
    assert store.cancel(ENGINE) is False
    assert partial_paths(store)[0].exists()
    assert store.cancel(ENGINE, discard=True) is False
    assert not any(p.exists() for p in partial_paths(store))


def test_disk_precheck_and_missing_storage(tmp_path):
    archive = make_archive()
    store, opener = store_for(tmp_path, archive, disk=Disk(free=1000))
    error = asyncio.run(failure(store, "disk_space"))
    assert "GiB of free space" in str(error) and not opener.requests
    store = ModelStore(None, engines=fake_catalog(archive), opener=opener)
    asyncio.run(failure(store, "storage"))
    assert store.state(ENGINE).state == "absent"


def test_symlinked_root_disables_downloads(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "models").symlink_to(tmp_path / "real")
    store = ModelStore(tmp_path / "models", engines=fake_catalog(make_archive()))
    assert store.root is None


def test_wrong_hash_leaves_no_final_directory_and_drops_partial(tmp_path):
    archive = make_archive()
    engines = fake_catalog(archive, sha={"joiner.int8.onnx": "0" * 64})
    store, _ = store_for(tmp_path, archive, engines=engines)
    asyncio.run(failure(store, "hash_mismatch"))
    assert not (store.root / ENGINE).exists()
    assert not any(p.exists() for p in partial_paths(store))
    assert not [p for p in store.root.iterdir() if p.name.startswith(".staging-")]


def test_unpinned_archive_is_refused_before_tarfile_opens_it(tmp_path, monkeypatch):
    archive = make_archive()
    engines = fake_catalog(archive, archive_sha="0" * 64)
    store, _ = store_for(tmp_path, archive, engines=engines)
    monkeypatch.setattr(model_store.tarfile, "open", lambda *a, **k: pytest.fail("opened"))
    error = asyncio.run(failure(store, "hash_mismatch"))
    assert "archive" in str(error)
    assert not any(p.exists() for p in partial_paths(store))


def test_corrupt_resumed_partial_is_caught_by_the_archive_pin(tmp_path):
    archive = make_archive()
    store, _ = store_for(tmp_path, archive)
    half = len(archive) // 2
    write_partial(store, b"\xff" * half, archive_bytes=len(archive))
    asyncio.run(failure(store, "hash_mismatch"))
    assert not any(p.exists() for p in partial_paths(store))
    asyncio.run(store.download(ENGINE))
    assert store.state(ENGINE).state == "ready"


ENCODER = f"{DIRECTORY}/encoder.int8.onnx"
BAD_MEMBERS = {
    "dotdot": [member(f"{DIRECTORY}/x/../encoder.int8.onnx", FILES["encoder.int8.onnx"])],
    "absolute": [member("/" + ENCODER, FILES["encoder.int8.onnx"])],
    "symlink": [member(ENCODER, kind=tarfile.SYMTYPE, linkname="/etc/passwd")],
    "hardlink": [member(ENCODER, kind=tarfile.LNKTYPE, linkname="/etc/passwd")],
    "device": [member(ENCODER, kind=tarfile.CHRTYPE)],
    "duplicate": [member(ENCODER, FILES["encoder.int8.onnx"])],
}


@pytest.mark.parametrize("case", sorted(BAD_MEMBERS))
def test_hostile_members_claiming_pinned_names_are_layout_errors(tmp_path, case):
    skip = () if case == "duplicate" else ("encoder.int8.onnx",)
    archive = make_archive(extra_members=BAD_MEMBERS[case], skip=skip)
    store, _ = store_for(tmp_path, archive)
    asyncio.run(failure(store, "archive_layout"))
    assert not (store.root / ENGINE).exists()
    assert not (tmp_path / "encoder.int8.onnx").exists()
    assert not partial_paths(store)[0].exists()


def test_wrong_member_size_and_missing_file(tmp_path):
    archive = make_archive()
    engines = fake_catalog(archive, size={"tokens.txt": 3})
    store, _ = store_for(tmp_path / "a", archive, engines=engines)
    asyncio.run(failure(store, "archive_layout"))
    archive = make_archive(skip=("tokens.txt",))
    store, _ = store_for(tmp_path / "b", archive, engines=fake_catalog(archive, FILES))
    error = asyncio.run(failure(store, "archive_layout"))
    assert "tokens.txt" in str(error)


def test_unreadable_archive_is_a_layout_error(tmp_path):
    archive = b"BZh9" + b"\x00" * 200
    store, _ = store_for(tmp_path, archive)
    asyncio.run(failure(store, "archive_layout"))


def test_failed_replacement_keeps_previous_install(tmp_path):
    archive = make_archive()
    store, _ = store_for(tmp_path, archive)
    asyncio.run(store.download(ENGINE))
    changed = dict(FILES, **{"tokens.txt": b"<blk> 0\nb 1\n"})
    bad = make_archive(changed)
    engines = fake_catalog(bad, changed, sha={"tokens.txt": "1" * 64})
    store, _ = store_for(tmp_path, bad, engines=engines)
    asyncio.run(failure(store, "hash_mismatch"))
    assert (store.root / ENGINE / "tokens.txt").read_bytes() == FILES["tokens.txt"]
    assert (store.root / ENGINE / "manifest.json").exists()


def test_reinstall_replaces_previous_version(tmp_path):
    archive = make_archive()
    store, _ = store_for(tmp_path, archive)
    asyncio.run(store.download(ENGINE))
    (store.root / ENGINE / "stale").write_text("x")
    asyncio.run(store.download(ENGINE))
    assert not (store.root / ENGINE / "stale").exists()
    assert sorted(p.name for p in store.root.iterdir()) == [".partial", ENGINE]


def test_startup_removes_staging_and_old_directories_only(tmp_path):
    root = tmp_path / "models"
    for name in (".staging-nemotron-en-1", ".old-nemotron-en-2", ENGINE, ".partial"):
        (root / name).mkdir(parents=True)
    ModelStore(root, engines=fake_catalog(make_archive()))
    assert sorted(p.name for p in root.iterdir()) == [".partial", ENGINE]


def test_concurrent_download_is_busy(tmp_path):
    archive = make_archive()
    gate = threading.Event()
    store, _ = store_for(tmp_path, archive, gate=gate)

    async def run():
        first = asyncio.create_task(store.download(ENGINE))
        await asyncio.sleep(0.05)
        assert store.busy == ENGINE
        assert store.state(ENGINE).state == "downloading"
        with pytest.raises(ModelError) as error:
            await store.download("nemotron-multilingual")
        assert error.value.code == "busy"
        with pytest.raises(ModelError) as error:
            await store.delete(ENGINE)
        assert error.value.code == "busy"
        gate.set()
        await first

    asyncio.run(run())
    assert store.state(ENGINE).state == "ready"


def test_cancelling_the_awaiting_task_stops_the_thread(tmp_path):
    archive = make_archive()
    gate = threading.Event()
    store, _ = store_for(tmp_path, archive, gate=gate)

    async def run():
        task = asyncio.create_task(store.download(ENGINE))
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.sleep(0.01)
        gate.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert store.busy is None

    asyncio.run(run())


def test_deep_verify_detects_a_flipped_byte_and_state_stays_cheap(tmp_path, monkeypatch):
    store, _ = store_for(tmp_path, make_archive())
    asyncio.run(store.download(ENGINE))
    assert asyncio.run(store.verify(ENGINE)).state == "ready"
    encoder = store.root / ENGINE / "encoder.int8.onnx"
    data = bytearray(encoder.read_bytes())
    data[5] ^= 1
    encoder.write_bytes(bytes(data))

    def forbidden(*args, **kwargs):
        raise AssertionError("state() must not hash")

    with monkeypatch.context() as patch:
        patch.setattr(model_store, "_hash_file", forbidden)
        patch.setattr(model_store.hashlib, "sha256", forbidden)
        assert store.state(ENGINE).state == "ready"
        assert asyncio.run(store.verify(ENGINE, deep=False)).state == "ready"
    result = asyncio.run(store.verify(ENGINE))
    assert result.state == "corrupt" and "encoder.int8.onnx" in result.error


def test_state_reports_corrupt_for_missing_files_or_foreign_manifest(tmp_path):
    store, _ = store_for(tmp_path, make_archive())
    asyncio.run(store.download(ENGINE))
    manifest = store.root / ENGINE / "manifest.json"
    data = json.loads(manifest.read_text())
    manifest.write_text(json.dumps(data | {"archive": "other.tar.bz2"}))
    assert store.state(ENGINE).state == "corrupt"
    manifest.write_text(json.dumps(data))
    (store.root / ENGINE / "tokens.txt").unlink()
    assert store.state(ENGINE).state == "corrupt"


def test_delete_removes_install_and_partial(tmp_path):
    store, _ = store_for(tmp_path, make_archive())
    asyncio.run(store.download(ENGINE))
    partial_paths(store)[0].write_bytes(b"x")
    asyncio.run(store.delete(ENGINE))
    assert store.state(ENGINE).state == "absent"
    assert not (store.root / ENGINE).exists()


def test_whistle_is_bundled(tmp_path, monkeypatch):
    monkeypatch.setenv("CASSINI_LIVE_WEIGHTS", str(tmp_path / "w" / "whistle.cact"))
    store = ModelStore(tmp_path / "models")
    assert store.state("whistle").state == "bundled"
    assert asyncio.run(store.download("whistle")) == tmp_path / "w"
    assert store.model_dir("whistle") == tmp_path / "w"
    asyncio.run(store.delete("whistle"))


def test_written_and_hashed_files_leave_the_page_cache(tmp_path, monkeypatch):
    # memory.current counts page cache; a download must not trip the 80% memory guard.
    dropped = []
    monkeypatch.setattr(model_store, "CACHE_DROP", 1)
    monkeypatch.setattr(model_store.os, "posix_fadvise", lambda fd, *a: dropped.append(a))
    store, _ = store_for(tmp_path, make_archive())
    asyncio.run(store.download(ENGINE))
    after_download = len(dropped)
    assert after_download >= len(FILES) + 2  # each file, the archive write and read
    assert asyncio.run(store.verify(ENGINE)).state == "ready"
    assert len(dropped) >= after_download + len(FILES)
    assert {a[-1] for a in dropped} == {model_store.os.POSIX_FADV_DONTNEED}


def test_pacer_sleeps_only_while_calls_are_active(monkeypatch):
    clock, sleeps, busy = [0.0], [], [False]
    monkeypatch.setattr(model_store.time, "thread_time", lambda: clock[0])
    monkeypatch.setattr(model_store.time, "sleep", sleeps.append)
    pace = model_store._Pacer(lambda: busy[0])
    clock[0] = 0.2
    pace()
    assert sleeps == []
    busy[0] = True
    clock[0] = 0.3
    pace()
    assert sleeps == [pytest.approx(model_store.PACE_SLEEP * 0.1)]
    clock[0] = 0.31
    pace()
    assert len(sleeps) == 1


def test_reinstall_counts_the_old_directory_in_the_disk_check(tmp_path):
    archive = make_archive()
    store, _ = store_for(tmp_path, archive)
    asyncio.run(store.download(ENGINE))
    (store.root / ENGINE / "tokens.txt").write_bytes(b"changed")
    installed = sum(p.stat().st_size for p in (store.root / ENGINE).iterdir())
    need = len(archive) + sum(len(d) for d in FILES.values()) + 256 * 1024 * 1024
    store.disk_usage = Disk(free=need + installed - 1)
    asyncio.run(failure(store, "disk_space"))
    store.disk_usage = Disk(free=need + installed)
    asyncio.run(store.download(ENGINE))
