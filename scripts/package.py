"""Build a reproducible source/manifest package without models, caches or secrets."""

import gzip
import os
import subprocess
import sys
import tarfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    subprocess.run([sys.executable, str(ROOT / "scripts/check_manifest.py")], check=True)
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    destination = ROOT / "dist" / f"cassini-live-{version}.tar.gz"
    destination.parent.mkdir(exist_ok=True)
    paths = [
        ROOT / name
        for name in (
            "LICENSE",
            "NOTICE.md",
            "README.md",
            "pyproject.toml",
            "uv.lock",
            "Dockerfile",
            ".dockerignore",
            ".gitignore",
        )
    ]
    for folder in (
        "appinfo",
        "img",
        "cassini_live",
        "scripts",
        "docs",
        "spec",
        "tests",
        "LICENSES",
        "native",
    ):
        paths.extend(
            p
            for p in (ROOT / folder).rglob("*")
            if p.is_file()
            and "__pycache__" not in p.parts
            and p.suffix
            in {
                ".py",
                ".c",
                ".md",
                ".svg",
                ".js",
                ".css",
                ".xml",
                ".xsd",
                ".xslt",
                ".wav",
                ".txt",
                ".png",
            }
        )
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    with destination.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=epoch) as gz:
        with tarfile.open(fileobj=gz, mode="w") as archive:
            for path in sorted(paths):
                info = archive.gettarinfo(
                    str(path), "live_transcription/" + str(path.relative_to(ROOT))
                )
                info.uid = info.gid = 0
                info.uname = info.gname = "root"
                info.mtime = epoch
                info.mode = 0o644
                with path.open("rb") as source:
                    archive.addfile(info, source)
    print(destination)


if __name__ == "__main__":
    main()
