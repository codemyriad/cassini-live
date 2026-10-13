"""Bake pinned Whistle weights and the matching CPU engine into the image.

The deployed container never needs access to a model registry.
"""

import hashlib
import json
import os
import platform
import shutil
import sys
import zipfile
from pathlib import Path

os.environ["NEEDLE_TELEMETRY"] = "0"
os.environ["DO_NOT_TRACK"] = "1"

from huggingface_hub import hf_hub_download  # noqa: E402

WEIGHTS_REVISION = "ca5287601bef25af26dcf2e1b2bdc0843a7c19e5"
ENGINE_REVISION = "93e88ede867e4311c39151285e74df513e4b835b"


def main():
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "models")
    target.mkdir(parents=True, exist_ok=True)
    architecture = {"x86_64": "x86_64", "aarch64": "aarch64"}[platform.machine()]
    weights = hf_hub_download("Cactus-Compute/whistle", "whistle.cact", revision=WEIGHTS_REVISION)
    shutil.copyfile(weights, target / "whistle.cact")
    engine = hf_hub_download(
        "Cactus-Compute/needle3",
        f"python/cactus_needle-3.3.0-py3-none-manylinux2014_{architecture}.whl",
        revision=ENGINE_REVISION,
    )
    with zipfile.ZipFile(engine) as archive:
        (target / "libneedle3.so").write_bytes(archive.read("needle/libneedle3.so"))
    os.environ["NEEDLE3_LIB_PATH"] = str((target / "libneedle3.so").resolve())
    import needle

    needle.Whistle(weights=target / "whistle.cact")  # Fail the build if loading fails.
    manifest = {
        "weightsRevision": WEIGHTS_REVISION,
        "engineRevision": ENGINE_REVISION,
        "engineVersion": "3.3.0",
        "architecture": architecture,
        "sha256": {
            name: hashlib.sha256((target / name).read_bytes()).hexdigest()
            for name in ("whistle.cact", "libneedle3.so")
        },
    }
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
