"""Validate Store metadata, ExApp proxy boundaries and release version alignment."""

import re
import tomllib
from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parents[1]


def main():
    manifest = etree.parse(str(ROOT / "appinfo/info.xml"))
    schema = etree.XMLSchema(etree.parse(str(ROOT / "spec/appstore/info.xsd")))
    transform = etree.XSLT(etree.parse(str(ROOT / "spec/appstore/pre-info.xslt")))
    schema.assertValid(transform(manifest))
    info = manifest.getroot()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert info.findtext("id") == "live_transcription", "Talk requires this provider ID"
    assert info.findtext("version") == version
    assert info.findtext("external-app/docker-install/image-tag") == version
    assert f'__version__ = "{version}"' in (ROOT / "cassini_live/__init__.py").read_text()
    variables = {e.text for e in info.findall("external-app/environment-variables/variable/name")}
    assert variables == {
        "CASSINI_TALK_SIGNALING_INTERNAL_SECRET",
        "CASSINI_TALK_HPB_URL",
        "CASSINI_LIVE_WORKERS",
    }
    routes = info.findall("external-app/routes/route")
    assert routes and all(r.findtext("access_level") == "USER" for r in routes)
    patterns = [r.findtext("url").replace(r"\/", "/") for r in routes]
    for private in ("api/v1/call/transcribe", "api/v1/call/leave", "enabled", "init"):
        assert not any(re.fullmatch(pattern, private) for pattern in patterns)
    for public in ("status", "ui/live.js", "ui/live.css", "img/app.svg"):
        assert any(re.fullmatch(pattern, public) for pattern in patterns)
    assert (ROOT / "img/app.svg").read_bytes() == (
        ROOT / "cassini_live/static/app.svg"
    ).read_bytes()
    print(f"Cassini Live {version}: Store metadata, versions and proxy boundaries pass")


if __name__ == "__main__":
    main()
