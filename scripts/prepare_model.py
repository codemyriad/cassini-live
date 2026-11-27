"""Bake pinned Whistle weights and the matching CPU engine into the image.

The deployed container never needs access to a model registry.
"""

import json
import os
import platform
import sys
import tempfile
import zipfile
from pathlib import Path

from prepare_assets import Asset, download_cache, file_sha256, install

os.environ["NEEDLE_TELEMETRY"] = "0"
os.environ["DO_NOT_TRACK"] = "1"

WEIGHTS_REVISION = "ca5287601bef25af26dcf2e1b2bdc0843a7c19e5"
ENGINE_REVISION = "93e88ede867e4311c39151285e74df513e4b835b"
WHISTLE = Asset(
    "whistle.cact",
    "b6e02f048568ac5d01a2042556c658061e699acbc0aa2a1439f52f3d461dffeb",
    16919407,
    "f66904115f6fade5fbc173b0d0c9c9131cb6b1213d9b5f189072186109186e4d",
    16034183,
)
NEEDLE = {
    "x86_64": Asset(
        "cactus_needle-3.3.0-py3-none-manylinux2014_x86_64.whl",
        "33717a889ecc8d8bb59a1ba3982635176a66479cac61297242b86b9a1c94dc5b",
        686463,
        "622a61e840b6040597cd8e8d600256b3b4e5e834dc8d3463beabaf057a7c1833",
        686561,
    ),
    "aarch64": Asset(
        "cactus_needle-3.3.0-py3-none-manylinux2014_aarch64.whl",
        "ba5b381186036765f554ddd402d7757576c66a3d8ee4056661b33f1f78d5d5e5",
        662839,
        "509f5280fc128c9dfd1f2ba2693ac584d1874561ca14182cc6051d9f4a60455a",
        662459,
    ),
}


def main():
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "models")
    target.mkdir(parents=True, exist_ok=True)
    architecture = platform.machine()
    engine = NEEDLE[architecture]
    cache = download_cache(target)
    install(WHISTLE, target / WHISTLE.name, cache)
    with tempfile.TemporaryDirectory(dir=target) as staging:
        wheel = Path(staging) / engine.name
        install(engine, wheel, cache)
        with zipfile.ZipFile(wheel) as archive:
            library = Path(staging) / "libneedle3.so"
            library.write_bytes(archive.read("needle/libneedle3.so"))
            library.replace(target / library.name)
    os.environ["NEEDLE3_LIB_PATH"] = str((target / "libneedle3.so").resolve())
    import needle

    needle.Whistle(weights=target / "whistle.cact")  # Fail the build if loading fails.
    manifest = {
        "weightsRevision": WEIGHTS_REVISION,
        "engineRevision": ENGINE_REVISION,
        "engineVersion": "3.3.0",
        "architecture": architecture,
        "sha256": {
            name: file_sha256(target / name)
            for name in ("whistle.cact", "libneedle3.so")
        },
    }
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
