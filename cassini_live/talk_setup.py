"""How Cassini Live reaches Talk's high-performance backend: its internal secret and an
optional URL override.

Nextcloud 34 and later deploy an App Store ExApp from the Apps page without asking for
deploy options, so both values can also be entered on the settings page after
installation. As in Cassini, a value set in deploy options always wins and the settings
page cannot change it; otherwise the saved value applies. The saved values live in one
file on the app's persistent volume. An unreadable file is reported and blocks changes:
it is never silently replaced, because that would forget a credential.
"""

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import websocket_url

DEPLOY = "deploy_options"
SAVED = "settings_page"
UNSET = "unset"
DISCOVERED = "talk"
MAX_SECRET = 256
MAX_URL = 512
SECRET_MISSING = (
    "Enter the Talk HPB internal secret on the Cassini Live settings page "
    "(Administration settings > Assistant, then ? next to Cassini Live)"
)


class SetupRefused(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code = status, code


@dataclass(frozen=True)
class Check:
    """The last HPB check: passed, failed or unreachable. Never holds the secret."""

    state: str
    message: str
    checked_at: float

    def to_json(self) -> dict:
        return {"state": self.state, "message": self.message, "checkedAt": self.checked_at}


class TalkSetup:
    def __init__(self, deployed_secret: str = "", deployed_url: str = "", path: Path | None = None):
        self.deployed_secret = deployed_secret.strip()
        self.deployed_url = deployed_url.strip()
        self.path = path
        self.unreadable = False
        self.check: Check | None = None
        self._saved = self._load()

    @classmethod
    def from_config(cls, config, path: Path | None = None) -> "TalkSetup":
        return cls(config.secret, config.hpb_url, path)

    def _load(self) -> dict:
        if self.path is None:
            return {}
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return {}
        except OSError:
            self.unreadable = True
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if not isinstance(data, dict) or not all(
            isinstance(data.get(key, ""), str) for key in ("secret", "hpbUrl")
        ):
            self.unreadable = True
            return {}
        return {key: data[key] for key in ("secret", "hpbUrl") if data.get(key)}

    def secret(self) -> tuple[str, str]:
        if self.deployed_secret:
            return self.deployed_secret, DEPLOY
        if self._saved.get("secret"):
            return self._saved["secret"], SAVED
        return "", UNSET

    def hpb_url(self) -> tuple[str, str]:
        """The override and where it comes from; empty means Talk's own HPB URL."""
        if self.deployed_url:
            return self.deployed_url, DEPLOY
        if self._saved.get("hpbUrl"):
            return self._saved["hpbUrl"], SAVED
        return "", DISCOVERED

    def status(self) -> dict:
        _, secret_source = self.secret()
        url, url_source = self.hpb_url()
        return {
            "secretConfigured": secret_source != UNSET,
            "secretSource": secret_source,
            "hpbUrl": url,
            "hpbUrlSource": url_source,
            "unreadable": self.unreadable,
            "storage": self.path is not None,
            "check": self.check.to_json() if self.check else None,
        }

    def save(self, data: dict) -> bool:
        """Apply the fields present in `data` ("secret", "hpbUrl"); an empty string clears
        one. Returns whether the effective connection values changed."""
        unknown = set(data) - {"secret", "hpbUrl"}
        if unknown or not data:
            raise SetupRefused(400, "bad_request", "Send the HPB internal secret or URL")
        if self.path is None:
            raise SetupRefused(
                409, "storage", "Cassini Live has no persistent storage to save this in"
            )
        if self.unreadable:
            raise SetupRefused(
                409,
                "unreadable",
                f"Cassini Live could not read {self.path.name} on its persistent volume. "
                "Restore it from a backup, or remove it to enter the values again",
            )
        before = (self.secret()[0], self.hpb_url()[0])
        saved = dict(self._saved)
        if "secret" in data:
            if self.deployed_secret:
                raise SetupRefused(
                    409, "deploy_options", "The HPB internal secret is set in deploy options"
                )
            saved["secret"] = clean_secret(data["secret"])
        if "hpbUrl" in data:
            if self.deployed_url:
                raise SetupRefused(
                    409, "deploy_options", "The HPB URL override is set in deploy options"
                )
            saved["hpbUrl"] = clean_url(data["hpbUrl"])
        saved = {key: value for key, value in saved.items() if value}
        self._write(saved)
        self._saved = saved
        changed = before != (self.secret()[0], self.hpb_url()[0])
        if changed:
            self.check = None
        return changed

    def _write(self, saved: dict) -> None:
        directory = self.path.parent
        try:
            directory.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".talk-setup-", dir=directory)
            try:
                with os.fdopen(fd, "w") as file:
                    json.dump(saved, file)
                    file.flush()
                    os.fsync(file.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, self.path)
            except BaseException:
                Path(temporary).unlink(missing_ok=True)
                raise
        except OSError:
            raise SetupRefused(
                500, "storage", "Could not save the setting on the persistent volume"
            ) from None


def clean_secret(value) -> str:
    if not isinstance(value, str):
        raise SetupRefused(400, "bad_request", "The HPB internal secret must be text")
    value = value.strip()
    if len(value) > MAX_SECRET or any(c.isspace() or not c.isprintable() for c in value):
        raise SetupRefused(
            400, "bad_secret", "Paste the internalsecret value only, without spaces or quotes"
        )
    return value


def clean_url(value) -> str:
    if not isinstance(value, str):
        raise SetupRefused(400, "bad_request", "The HPB URL must be text")
    value = value.strip()
    if not value:
        return ""
    if len(value) > MAX_URL:
        raise SetupRefused(400, "bad_url", "The HPB URL is too long")
    try:
        websocket_url(value)
    except ValueError as error:
        raise SetupRefused(400, "bad_url", str(error)) from None
    return value
