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
        "CASSINI_LIVE_DEBUG_VIDEO",
    }
    routes = info.findall("external-app/routes/route")
    assert routes
    table = [
        (r.findtext("url").replace(r"\/", "/"), r.findtext("verb"), r.findtext("access_level"))
        for r in routes
    ]
    for pattern, _, level in table:
        if pattern == "^/?status$":
            assert level == "USER", "status is the only user route"
        else:
            assert level == "ADMIN" and pattern.startswith("^/?settings/"), pattern
    assert [p for p, _, level in table if level == "USER"] == ["^/?status$"]

    # AppAPI matches "/" + path (and the bare path, deprecated); HaRP matches "/" + path.
    def matches(path, verb=None):
        return [
            p for p, v, _ in table
            if (verb is None or verb in v.split(","))
            and any(re.fullmatch(p, candidate) for candidate in (path, "/" + path))
        ]

    private = (
        "api/v1/call/transcribe", "api/v1/call/leave", "api/v1/call/set-language", "enabled",
        "init", "capacity/check", "ui/live.js", "ui/live.css", "img/app.svg", "heartbeat",
    )
    for path in private:
        assert not matches(path), f"{path} must stay server-to-server"
    public = {
        "status": "GET",
        "settings/index.html": "GET",
        "settings/app.js": "GET",
        "settings/app.css": "GET",
        "settings/api/state": "GET",
        "settings/api/engine": "POST",
        "settings/api/cancel": "POST",
        "settings/api/capacity": "POST",
        "settings/api/models/nemotron-en": "DELETE",
        "settings/licenses/NVIDIA-OML.txt": "GET",
    }
    for path, verb in public.items():
        assert matches(path, verb), f"{verb} {path} is not routed"
    print(f"Cassini Live {version}: Store metadata, versions and proxy boundaries pass")


if __name__ == "__main__":
    main()
