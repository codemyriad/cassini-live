"""Validate Store metadata, ExApp proxy boundaries and release version alignment.

One manifest ships from this repo: appinfo/info.xml, the App Store package's
manifest under the app id `cassini_live`.
"""

import re
import tomllib
from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parents[1]


STORE_ID = "cassini_live"


def main():
    manifest = etree.parse(str(ROOT / "appinfo/info.xml"))
    schema = etree.XMLSchema(etree.parse(str(ROOT / "spec/appstore/info.xsd")))
    transform = etree.XSLT(etree.parse(str(ROOT / "spec/appstore/pre-info.xslt")))
    schema.assertValid(transform(manifest))
    info = manifest.getroot()
    assert info.findtext("id") == STORE_ID, f"appinfo/info.xml must use the id {STORE_ID}"
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert info.findtext("version") == version
    assert info.findtext("external-app/docker-install/image-tag") == version
    assert f'__version__ = "{version}"' in (ROOT / "cassini_live/__init__.py").read_text()
    variables = {e.text for e in info.findall("external-app/environment-variables/variable/name")}
    assert variables == {
        "CASSINI_TALK_SIGNALING_INTERNAL_SECRET",
        "CASSINI_TALK_HPB_URL",
        "CASSINI_LIVE_DEBUG_VIDEO",
    }
    routes = info.findall("external-app/routes/route")
    assert routes
    table = [
        (r.findtext("url").replace(r"\/", "/"), r.findtext("verb"), r.findtext("access_level"))
        for r in routes
    ]
    user_patterns = [
        "^/?status$",
        "^/?history$",
        "^/?captions/transcribe$",
        r"^/?panel/panel\.js$",
        r"^/?ui/home\.js$",
        r"^/?img/app\.svg$",
    ]
    for pattern, verb, level in table:
        if pattern in user_patterns:
            assert level == "USER", pattern
            if pattern.startswith(("^/?panel/", "^/?ui/", "^/?img/")):
                assert verb == "GET", "the panel and the home page serve read-only static content"
            if pattern == "^/?captions/transcribe$":
                assert verb == "POST", "the caption toggle is POST only"
        else:
            assert level == "ADMIN", pattern
            assert pattern.startswith(("^/?settings/", "^/?diagnostics/")), pattern
            if pattern.startswith("^/?diagnostics/"):
                assert verb == "GET", "the diagnostics page and its feed are read-only"
    assert [p for p, _, level in table if level == "USER"] == user_patterns

    # AppAPI matches "/" + path (and the bare path, deprecated); HaRP matches "/" + path.
    def matches(path, verb=None):
        return [
            p for p, v, _ in table
            if (verb is None or verb in v.split(","))
            and any(re.fullmatch(p, candidate) for candidate in (path, "/" + path))
        ]

    private = (
        "api/v1/call/transcribe", "api/v1/call/leave", "api/v1/call/set-language", "enabled",
        "init", "capacity/check", "ui/live.js", "ui/live.css", "img/other.svg", "heartbeat",
    )
    for path in private:
        assert not matches(path), f"{path} must stay server-to-server"
    public = {
        "status": "GET",
        "captions/transcribe": "POST",
        "ui/home.js": "GET",
        "img/app.svg": "GET",
        "settings/index.html": "GET",
        "settings/app.js": "GET",
        "settings/app.css": "GET",
        "settings/api/state": "GET",
        "settings/api/engine": "POST",
        "settings/api/cancel": "POST",
        "settings/api/capacity": "POST",
        "settings/api/talk": "POST",
        "settings/api/talk/check": "POST",
        "settings/api/models/nemotron-en": "DELETE",
        "settings/licenses/NVIDIA-OML.txt": "GET",
        "diagnostics/": "GET",
        "diagnostics/index.html": "GET",
        "diagnostics/app.js": "GET",
        "diagnostics/app.css": "GET",
        "diagnostics/api/feed": "GET",
    }
    for path, verb in public.items():
        assert matches(path, verb), f"{verb} {path} is not routed"
    assert not matches("diagnostics/api/feed", "POST"), "POST diagnostics/api/feed is routed"
    assert not matches("diagnostics/other.js"), "diagnostics/other.js is routed"
    assert not matches("captions/transcribe", "GET"), "GET captions/transcribe is routed"
    assert not matches("settings/api/talk", "GET"), "GET settings/api/talk is routed"
    print(f"Cassini Live {version}: Store metadata, versions and proxy boundaries pass")


if __name__ == "__main__":
    main()
