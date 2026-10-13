"""Deployment defaults. No user account, API key or GPU is needed."""

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .resources import limits

LANGUAGE_NAMES = {
    "en": "English",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "nl": "Dutch",
    "pl": "Polish",
}
LANGUAGES = {
    code: {"name": name, "metadata": {"separator": " ", "rtl": False}}
    for code, name in LANGUAGE_NAMES.items()
}


def websocket_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.hostname:
        raise ValueError("Talk's high-performance backend URL is invalid")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            "Talk's high-performance backend URL must not contain credentials or queries"
        )
    path = parsed.path.rstrip("/")
    if not path.endswith("/spreed"):
        path += "/spreed"
    return urlunsplit(
        (
            {"https": "wss", "http": "ws"}.get(parsed.scheme, parsed.scheme),
            parsed.netloc,
            path,
            "",
            "",
        )
    )


@dataclass(frozen=True)
class Config:
    weights: str | None
    workers: int
    secret: str
    hpb_url: str

    @classmethod
    def from_env(cls):
        cpus = limits()["effectiveCpus"]
        workers = int(os.environ.get("CASSINI_LIVE_WORKERS") or min(4, max(1, int(cpus // 2))))
        if not 1 <= workers <= 32:
            raise ValueError("CASSINI_LIVE_WORKERS must be between 1 and 32")
        weights = os.environ.get("CASSINI_LIVE_WEIGHTS")
        baked = Path("/opt/whistle/whistle.cact")
        if not weights and baked.is_file():
            weights = str(baked)
        return cls(
            weights,
            workers,
            os.environ.get("CASSINI_TALK_SIGNALING_INTERNAL_SECRET", ""),
            os.environ.get("CASSINI_TALK_HPB_URL", ""),
        )
