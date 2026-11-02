import asyncio
import json
import threading

import pytest

from cassini_live import model_store
from cassini_live.model_store import ModelError, ModelStore
from tests.fakes_models import (
    FILES,
    Disk,
    FakeOpener,
    compress,
    fake_catalog,
    make_objects,
)

ENGINE = "nemotron-en"
ENCODER = "encoder.int8.onnx"


def store_for(tmp_path, objects=None, opener=None, engines=None, disk=None, **opener_args):
    objects = make_objects() if objects is None else objects
    opener = opener or FakeOpener(objects, **opener_args)
    store = ModelStore(tmp_path / "models", engines=engines or fake_catalog(objects),
                       opener=opener, disk_usage=disk or Disk(), clock=lambda: 1760000000)
    return store, opener


def partial_paths(store, name=ENCODER):
    partial = store.root / ".partial" / ENGINE / f"{name}.zst"
    return partial, partial.with_name(partial.name + ".json")


def no_partial(store):
    return not (store.root / ".partial" / ENGINE).exists()


async def failure(store, code):
    with pytest.raises(ModelError) as error:
        await store.download(ENGINE)
    assert error.value.code == code, str(error.value)
    return error.value


def test_happy_path_installs_atomically_with_license_and_manifest(tmp_path):
    objects = make_objects()
    samples = []

    def sample(_response):
        progress = store.progress(ENGINE)
        samples.append((progress.phase, progress.bytes_done, progress.bytes_total))

    store, opener = store_for(tmp_path, objects, on_read=sample)
    assert store.state(ENGINE).state == "absent"
    final = asyncio.run(store.download(ENGINE))

    assert final == store.model_dir(ENGINE) == store.root / ENGINE
    for name, data in FILES.items():
        assert (final / name).read_bytes() == data
    assert (final / "NOTICE.txt").read_text() == (
        "Licensed by NVIDIA Corporation under the NVIDIA Open Model License\n"
    )
    assert "NVIDIA Open Model License Agreement" in (final / "LICENSE.txt").read_text()
    manifest = json.loads((final / "manifest.json").read_text())
    assert manifest["source"] == "https://dist.gocassini.com/models/files/"
    assert manifest["downloads"][ENCODER]["bytes"] == len(objects[ENCODER])
    assert manifest["licenseVersion"] == "2025-10-24"
    assert manifest["installedAt"] == 1760000000
    assert no_partial(store)
    assert sorted(p.name for p in store.root.iterdir()) == [".partial", ENGINE]
    total = sum(len(o) for o in objects.values())
    assert samples and all(s[0] == "downloading" and s[2] == total for s in samples)
    assert [s[1] for s in samples] == sorted(s[1] for s in samples)
    assert [r.full_url.rsplit("/", 1)[1] for r in opener.requests] == [
        f"{name}.zst" for name in FILES
    ]
    assert opener.requests[0].get_header("User-agent").startswith("cassini-live/")
    assert opener.requests[0].get_header("Accept-encoding") == "identity"
    assert store.state(ENGINE).state == "ready" and store.busy is None
    assert store.progress(ENGINE) is None


def test_content_length_mismatch_is_refused_before_writing(tmp_path):
    store, _ = store_for(tmp_path, content_length=10**6)
    error = asyncio.run(failure(store, "size_mismatch"))
    assert "published model file changed" in str(error)
    assert not partial_paths(store)[0].exists()


def test_body_longer_than_pinned_size_is_never_written_past_it(tmp_path):
    objects = make_objects()
    pinned = len(objects[ENCODER]) - 10
    engines = fake_catalog(objects, zst_bytes={ENCODER: pinned})
    store, _ = store_for(tmp_path, objects, engines=engines, content_length=pinned)
    asyncio.run(failure(store, "size_mismatch"))
    assert partial_paths(store)[0].stat().st_size == pinned


def write_partial(store, data, etag='"v1"', size=None, name=ENCODER):
    partial, sidecar = partial_paths(store, name)
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_bytes(data)
    sidecar.write_text(json.dumps({"url": "x", "etag": etag, "lastModified": None,
                                   "bytes": size}))


def test_resume_with_206_appends_and_skips_finished_objects(tmp_path):
    objects = make_objects()
    store, opener = store_for(tmp_path, objects)
    decoder = objects["decoder.int8.onnx"]
    write_partial(store, objects[ENCODER], size=len(objects[ENCODER]))
    half = len(decoder) // 2
    write_partial(store, decoder[:half], size=len(decoder), name="decoder.int8.onnx")
    assert store.state(ENGINE).state == "partial"
    assert store.state(ENGINE).partial_bytes == len(objects[ENCODER]) + half
    asyncio.run(store.download(ENGINE))
    request = opener.requests[0]
    assert request.full_url.endswith("/decoder.int8.onnx.zst")
    assert request.get_header("Range") == f"bytes={half}-"
    assert request.get_header("If-range") == '"v1"'
    assert len(opener.requests) == 3
    assert store.state(ENGINE).state == "ready"


@pytest.mark.parametrize("args", [{"ignore_range": True}, {"etag": '"v2"'}])
def test_server_ignoring_range_or_changed_validator_restarts(tmp_path, args):
    objects = make_objects()
    store, opener = store_for(tmp_path, objects, **args)
    write_partial(store, b"garbage" * 20, size=len(objects[ENCODER]))
    asyncio.run(store.download(ENGINE))
    assert opener.requests[0].get_header("Range") == "bytes=140-"
    assert store.state(ENGINE).state == "ready"


def test_partial_without_validator_starts_over(tmp_path):
    objects = make_objects()
    store, opener = store_for(tmp_path, objects)
    write_partial(store, b"x" * 50, etag=None, size=len(objects[ENCODER]))
    asyncio.run(store.download(ENGINE))
    assert opener.requests[0].get_header("Range") is None


def test_interrupted_download_keeps_partial_and_resumes(tmp_path):
    store, opener = store_for(tmp_path, stall=100)
    asyncio.run(failure(store, "network"))
    assert partial_paths(store)[0].stat().st_size == 100
    assert store.state(ENGINE).state == "partial"
    assert store.state(ENGINE).error
    opener.stall = None
    asyncio.run(store.download(ENGINE))
    assert opener.requests[1].get_header("Range") == "bytes=100-"
    assert store.state(ENGINE).state == "ready"


def test_http_error_is_network(tmp_path):
    store, _ = store_for(tmp_path, status=404)
    error = asyncio.run(failure(store, "network"))
    assert "404" in str(error)
    assert store.state(ENGINE).state == "error"


@pytest.mark.parametrize("discard", [False, True])
def test_cancel_keeps_partial_unless_discarded(tmp_path, discard):
    def cancel(response):
        if response.position >= 100:
            assert store.cancel(ENGINE, discard=discard)

    store, _ = store_for(tmp_path, on_read=cancel)
    asyncio.run(failure(store, "cancelled"))
    assert partial_paths(store)[0].exists() is not discard
    assert store.busy is None
    assert store.state(ENGINE).error is None


def test_cancel_when_idle_with_discard_removes_partial(tmp_path):
    store, _ = store_for(tmp_path)
    write_partial(store, b"x" * 10)
    assert store.cancel(ENGINE) is False
    assert partial_paths(store)[0].exists()
    assert store.cancel(ENGINE, discard=True) is False
    assert no_partial(store)


def test_disk_precheck_and_missing_storage(tmp_path):
    objects = make_objects()
    store, opener = store_for(tmp_path, objects, disk=Disk(free=1000))
    error = asyncio.run(failure(store, "disk_space"))
    assert "GiB of free space" in str(error) and not opener.requests
    store = ModelStore(None, engines=fake_catalog(objects), opener=opener)
    asyncio.run(failure(store, "storage"))
    assert store.state(ENGINE).state == "absent"


def test_symlinked_root_disables_downloads(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "models").symlink_to(tmp_path / "real")
    store = ModelStore(tmp_path / "models", engines=fake_catalog(make_objects()))
    assert store.root is None


def test_wrong_file_hash_leaves_no_final_directory_and_drops_partial(tmp_path):
    objects = make_objects()
    engines = fake_catalog(objects, sha={"joiner.int8.onnx": "0" * 64})
    store, _ = store_for(tmp_path, objects, engines=engines)
    asyncio.run(failure(store, "hash_mismatch"))
    assert not (store.root / ENGINE).exists()
    assert no_partial(store)
    assert not [p for p in store.root.iterdir() if p.name.startswith(".staging-")]


def test_unpinned_object_is_refused_before_it_is_decoded(tmp_path, monkeypatch):
    objects = make_objects()
    engines = fake_catalog(objects, zst_sha={"tokens.txt": "0" * 64})
    store, _ = store_for(tmp_path, objects, engines=engines)
    monkeypatch.setattr(model_store.zstandard, "ZstdDecompressor",
                        lambda *a, **k: pytest.fail("decoded"))
    error = asyncio.run(failure(store, "hash_mismatch"))
    assert "tokens.txt" in str(error)
    assert no_partial(store)


def test_corrupt_resumed_partial_is_caught_by_the_object_pin(tmp_path):
    objects = make_objects()
    store, _ = store_for(tmp_path, objects)
    half = len(objects[ENCODER]) // 2
    write_partial(store, b"\xff" * half, size=len(objects[ENCODER]))
    asyncio.run(failure(store, "hash_mismatch"))
    assert no_partial(store)
    asyncio.run(store.download(ENGINE))
    assert store.state(ENGINE).state == "ready"


@pytest.mark.parametrize("case", ["size", "longer", "undecodable"])
def test_pinned_object_that_decodes_wrong_is_a_hash_mismatch(tmp_path, case):
    objects = make_objects()
    if case == "size":  # the pinned size disagrees with what decodes
        engines = fake_catalog(objects, size={"tokens.txt": 3})
    elif case == "longer":
        objects["tokens.txt"] = compress(FILES["tokens.txt"] * 2)
        engines = fake_catalog(objects)
    else:
        objects["tokens.txt"] = b"not zstd at all"
        engines = fake_catalog(objects)
    store, _ = store_for(tmp_path, objects, engines=engines)
    error = asyncio.run(failure(store, "hash_mismatch"))
    assert "tokens.txt" in str(error)
    assert not (store.root / ENGINE).exists() and no_partial(store)


def test_failed_replacement_keeps_previous_install(tmp_path):
    store, _ = store_for(tmp_path)
    asyncio.run(store.download(ENGINE))
    changed = dict(FILES, **{"tokens.txt": b"<blk> 0\nb 1\n"})
    bad = make_objects(changed)
    engines = fake_catalog(bad, changed, sha={"tokens.txt": "1" * 64})
    store, _ = store_for(tmp_path, bad, engines=engines)
    asyncio.run(failure(store, "hash_mismatch"))
    assert (store.root / ENGINE / "tokens.txt").read_bytes() == FILES["tokens.txt"]
    assert (store.root / ENGINE / "manifest.json").exists()


def test_reinstall_replaces_previous_version(tmp_path):
    store, _ = store_for(tmp_path)
    asyncio.run(store.download(ENGINE))
    (store.root / ENGINE / "stale").write_text("x")
    asyncio.run(store.download(ENGINE))
    assert not (store.root / ENGINE / "stale").exists()
    assert sorted(p.name for p in store.root.iterdir()) == [".partial", ENGINE]


def test_startup_removes_staging_old_directories_and_old_archives_only(tmp_path):
    root = tmp_path / "models"
    for name in (".staging-nemotron-en-1", ".old-nemotron-en-2", ENGINE, ".partial/" + ENGINE):
        (root / name).mkdir(parents=True)
    old = "sherpa-onnx-nemotron-speech-streaming-en-0.6b-560ms-int8-2026-04-25.tar.bz2"
    (root / ".partial" / old).write_bytes(b"x")
    (root / ".partial" / (old + ".json")).write_text("{}")
    (root / ".partial" / ENGINE / "tokens.txt.zst").write_bytes(b"x")
    ModelStore(root, engines=fake_catalog(make_objects()))
    assert sorted(p.name for p in root.iterdir()) == [".partial", ENGINE]
    assert [p.name for p in (root / ".partial").iterdir()] == [ENGINE]
    assert (root / ".partial" / ENGINE / "tokens.txt.zst").exists()


def test_install_from_the_old_archive_download_stays_ready(tmp_path):
    # 0.8.2 and earlier unpacked the GitHub .tar.bz2; the files are byte-identical.
    store, opener = store_for(tmp_path)
    final = store.root / ENGINE
    final.mkdir(parents=True)
    for name, data in FILES.items():
        (final / name).write_bytes(data)
    pins = {f.name: {"sha256": f.sha256, "size": f.size}
            for f in store.engines[ENGINE].asset.files}
    (final / "manifest.json").write_text(json.dumps({
        "engine": ENGINE, "archive": f"{ENGINE}.tar.bz2", "url": "https://github.com/x",
        "archiveBytes": 1, "archiveSha256Observed": "a" * 64, "files": pins,
        "license": "NVIDIA-OML", "licenseVersion": "2025-10-24", "installedAt": 1,
    }))
    assert store.state(ENGINE).state == "ready"
    assert asyncio.run(store.verify(ENGINE)).state == "ready"
    assert not opener.requests


def test_concurrent_download_is_busy(tmp_path):
    gate = threading.Event()
    store, _ = store_for(tmp_path, gate=gate)

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
    gate = threading.Event()
    store, _ = store_for(tmp_path, gate=gate)

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
    store, _ = store_for(tmp_path)
    asyncio.run(store.download(ENGINE))
    assert asyncio.run(store.verify(ENGINE)).state == "ready"
    encoder = store.root / ENGINE / ENCODER
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
    assert result.state == "corrupt" and ENCODER in result.error


def test_state_reports_corrupt_for_missing_files_or_foreign_manifest(tmp_path):
    store, _ = store_for(tmp_path)
    asyncio.run(store.download(ENGINE))
    manifest = store.root / ENGINE / "manifest.json"
    data = json.loads(manifest.read_text())
    manifest.write_text(json.dumps(data | {"engine": "nemotron-multilingual"}))
    assert store.state(ENGINE).state == "corrupt"
    pins = dict(data["files"], **{"tokens.txt": {"sha256": "0" * 64, "size": 12}})
    manifest.write_text(json.dumps(data | {"files": pins}))
    assert store.state(ENGINE).state == "corrupt"
    manifest.write_text(json.dumps(data))
    (store.root / ENGINE / "tokens.txt").unlink()
    assert store.state(ENGINE).state == "corrupt"


def test_delete_removes_install_and_partial(tmp_path):
    store, _ = store_for(tmp_path)
    asyncio.run(store.download(ENGINE))
    write_partial(store, b"x")
    asyncio.run(store.delete(ENGINE))
    assert store.state(ENGINE).state == "absent"
    assert not (store.root / ENGINE).exists() and no_partial(store)


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
    store, _ = store_for(tmp_path)
    asyncio.run(store.download(ENGINE))
    after_download = len(dropped)
    # each object written, hashed and read for decoding, and each decoded file
    assert after_download >= 4 * len(FILES)
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
    objects = make_objects()
    store, _ = store_for(tmp_path, objects)
    asyncio.run(store.download(ENGINE))
    (store.root / ENGINE / "tokens.txt").write_bytes(b"changed")
    installed = sum(p.stat().st_size for p in (store.root / ENGINE).iterdir())
    need = (sum(len(o) for o in objects.values()) + sum(len(d) for d in FILES.values())
            + 256 * 1024 * 1024)
    store.disk_usage = Disk(free=need + installed - 1)
    asyncio.run(failure(store, "disk_space"))
    store.disk_usage = Disk(free=need + installed)
    asyncio.run(store.download(ENGINE))
