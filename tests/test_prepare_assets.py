import hashlib
import io

import pytest
import zstandard

from scripts import prepare_assets


def test_interrupted_asset_download_resumes_before_installing(monkeypatch, tmp_path):
    original = b"model weights"
    compressed = zstandard.ZstdCompressor().compress(original)
    pin = prepare_assets.Asset(
        "weights.bin",
        hashlib.sha256(original).hexdigest(),
        len(original),
        hashlib.sha256(compressed).hexdigest(),
        len(compressed),
    )
    target = tmp_path / pin.name
    target.write_bytes(b"previous version")
    cache = tmp_path / "cache"
    split = 10

    class Response(io.BytesIO):
        pass

    def open_asset(request, timeout):
        assert request.full_url == f"{prepare_assets.DIST_URL}{pin.zst_sha256}/{pin.name}.zst"
        if request.has_header("Range"):
            assert request.get_header("Range") == f"bytes={split}-"
            assert request.get_header("If-range") == f'"{pin.zst_sha256}"'
            response = Response(compressed[split:])
            response.status = 206
            response.headers = {
                "Content-Range": f"bytes {split}-{len(compressed) - 1}/{len(compressed)}",
                "Content-Length": str(len(compressed) - split),
            }
        else:
            response = Response(compressed[:split])
            response.status = 200
            response.headers = {"Content-Length": str(len(compressed))}
        return response

    monkeypatch.setattr(prepare_assets, "urlopen", open_asset)
    with pytest.raises(RuntimeError, match="Download ended early"):
        prepare_assets.install(pin, target, cache)
    assert target.read_bytes() == b"previous version"

    prepare_assets.install(pin, target, cache)
    assert target.read_bytes() == original
