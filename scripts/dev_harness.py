#!/usr/bin/env python3
"""Build and register Cassini Live against an explicitly configured running harness."""

import argparse
import base64
import json
import math
import os
import re
import secrets
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
LABEL = "io.codemyriad.cassini-live.dev-harness"
# Every deployment option has both a flag and LIVE_DEV_<UPPER_SNAKE_CASE> env var.
DEFAULTS = {
    "nextcloud-container": "spreedtest-nextcloud-1",
    "signaling-container": "spreedtest-signaling-1",
    "network": "spreedtest_default",
    "nextcloud-url": "http://nextcloud",
    "public-url": "http://localhost:28080",
    "hpb-url": None,
    "hpb-secret": None,
    "admin-user": "admin",
    "admin-password": "admin",
    "container": "nc_app_live_transcription",
    "daemon": "cassini_live_dev",
    "image": "cassini-live:dev",
    "aa-version": "5.0.0",
    "cpus": "4",
    "memory": "2g",
    "pids-limit": "128",
    "volume": "cassini_live_dev_data",
    "timeout": "180",
    "build-timeout": "3600",
    "app-secret": None,
    "check-only": "false",
}


# Executed inside the built image; no host Python dependencies or helper files.
HPB_PROBE = """
import asyncio
import json
from nc_py_api import AsyncNextcloudApp
from websockets.asyncio.client import connect
from cassini_live.config import Config
from cassini_live.service import CaptionService
from cassini_live.talk.client import SpreedClient
from cassini_live.talk.utils import get_ssl_context

async def check():
    service = CaptionService(Config.from_env())
    await service.discover(AsyncNextcloudApp())
    client = SpreedClient("unused-deployment-check", service.settings, "auto", None)
    url = service.settings.server
    tls = get_ssl_context(url)
    async with connect(url, **({"ssl": tls} if tls else {})) as ws:
        client._server = ws
        await client.send_hello()
        async with asyncio.timeout(15):
            while True:
                message = json.loads(await ws.recv())
                if message["type"] == "hello":
                    break
                if message["type"] != "welcome":
                    raise RuntimeError("HPB rejected authentication")
        await ws.send(json.dumps({"type": "bye"}))

asyncio.run(check())
"""


class Failure(Exception):
    pass


def config(argv=None, environ=None):
    env = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(description=__doc__)
    for key, default in DEFAULTS.items():
        variable = "LIVE_DEV_" + key.upper().replace("-", "_")
        description = "required" if default is None else f"default: {default}"
        if key == "app-secret":
            description = "default: generate a fresh credential"
        kwargs = {"help": f"{variable}; {description}", "default": argparse.SUPPRESS}
        if key == "check-only":
            kwargs.update(nargs="?", const="true", choices=("true", "false"))
        parser.add_argument("--" + key, **kwargs)
    values = vars(parser.parse_args(argv))
    for key, default in DEFAULTS.items():
        dest = key.replace("-", "_")
        variable = "LIVE_DEV_" + dest.upper()
        if dest not in values:
            values[dest] = env.get(variable, default)
        value = values[dest]
        if (value is None or not value.strip()) and key != "app-secret":
            parser.error(f"--{key} / {variable} is required and must not be empty")
        if key == "app-secret" and value is not None and not value.strip():
            parser.error(f"--{key} / {variable} must not be empty")
    for key in ("nextcloud_container", "signaling_container", "container", "network",
                "volume", "daemon"):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", values[key]):
            parser.error(f"invalid {key.replace('_', '-')} name")
    for key in ("nextcloud_url", "public_url", "hpb_url"):
        parsed = urlsplit(values[key])
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            parser.error(f"{key.replace('_', '-')} must be an HTTP(S) URL without credentials")
        values[key] = values[key].rstrip("/")
    for key, convert in (("cpus", float), ("pids_limit", int),
                         ("timeout", int), ("build_timeout", int)):
        try:
            number = convert(values[key])
            if not math.isfinite(number) or number <= 0:
                raise ValueError
            values[key] = number
        except ValueError:
            parser.error(f"{key.replace('_', '-')} must be a positive number")
    if not re.fullmatch(r"[1-9][0-9]*[bkmgBKMG]?", values["memory"]):
        parser.error("memory must be a positive Docker size, e.g. 2g or 2048m")
    if values["check_only"] not in {"true", "false"}:
        parser.error("check-only / LIVE_DEV_CHECK_ONLY must be true or false")
    values["check_only"] = values["check_only"] == "true"
    return argparse.Namespace(**values)


def manifest():
    root = ET.parse(ROOT / "appinfo/info.xml").getroot()
    routes = [{"url": r.findtext("url"), "verb": r.findtext("verb"),
               "access_level": {"PUBLIC": 0, "USER": 1, "ADMIN": 2}[r.findtext("access_level")]}
              for r in root.findall("external-app/routes/route")]
    app = {"appid": root.findtext("id"), "name": root.findtext("name"),
           "version": root.findtext("version"), "routes": routes}
    if not all(app.values()):
        raise Failure("Incomplete appinfo/info.xml")
    return app


class Harness:
    def __init__(self, cfg):
        self.cfg = cfg
        self.app = manifest()
        self.owner = json.dumps([cfg.nextcloud_container, cfg.daemon], separators=(",", ":"))
        self.existing = False
        self.registered = False
        self.daemon_exists = False

    def run(self, args, *, timeout=None, input=None, stream=False, env=None):
        # Never echo command arguments or captured output: registration contains credentials.
        try:
            result = subprocess.run(args, input=input, text=True, env=env,
                                    capture_output=not stream,
                                    timeout=timeout or self.cfg.timeout)
        except subprocess.TimeoutExpired:
            raise Failure("Command timed out; inspect the configured containers") from None
        if result.returncode:
            raise Failure(f"{args[0]} command failed (exit {result.returncode}); "
                          "inspect the configured containers")
        return result.stdout or ""

    def docker(self, *args, **kwargs):
        return self.run(["docker", *args], **kwargs)

    def occ(self, *args, **kwargs):
        return self.docker("exec", "-u", "www-data", self.cfg.nextcloud_container,
                           "php", "occ", "--no-ansi", *args, **kwargs)

    def page(self, path):
        cfg = self.cfg
        auth = base64.b64encode(f"{cfg.admin_user}:{cfg.admin_password}".encode()).decode()
        req = Request(cfg.public_url + path, headers={"Authorization": "Basic " + auth,
                                                        "OCS-APIRequest": "true"})
        with urlopen(req, timeout=min(cfg.timeout, 15)) as response:
            if response.status != 200 or response.url != req.full_url:
                raise Failure("Public URL check failed or redirected; check URL and credentials")
            return response.read()

    def preflight(self):
        cfg = self.cfg
        self.docker("info", "--format", "{{.ServerVersion}}")
        for name in (cfg.nextcloud_container, cfg.signaling_container):
            try:
                info = json.loads(self.docker("container", "inspect", name))[0]
            except Failure:
                raise Failure(f"Cannot inspect required harness container: {name}") from None
            if not info["State"]["Running"]:
                raise Failure(f"Required container is stopped: {name}")
            if info["State"].get("Health", {}).get("Status") == "unhealthy":
                raise Failure(f"Required container is unhealthy: {name}")
            if name == cfg.nextcloud_container:
                if cfg.network not in info["NetworkSettings"]["Networks"]:
                    raise Failure("Nextcloud is not attached to the configured network")
        try:
            self.docker("network", "inspect", cfg.network)
        except Failure:
            raise Failure(f"Required harness network is unavailable: {cfg.network}") from None
        status = json.loads(self.occ("status", "--output=json"))
        if not status.get("installed") or status.get("maintenance") or status.get("needsDbUpgrade"):
            raise Failure("Nextcloud must be installed, out of maintenance and up to date")
        apps = json.loads(self.occ("app:list", "--output=json"))["enabled"]
        if not {"spreed", "app_api"} <= apps.keys():
            raise Failure("Enable Talk (spreed) and AppAPI before running this script")
        actual_url = self.occ("config:system:get", "overwrite.cli.url").strip().rstrip("/")
        if actual_url != cfg.public_url:
            raise Failure("overwrite.cli.url differs from --public-url; correct it with "
                          "docker exec -u www-data <nextcloud-container> php occ "
                          "config:system:set overwrite.cli.url --value <public-url>")
        # This OCS endpoint requires an authenticated administrator.
        users = json.loads(self.page("/ocs/v2.php/cloud/users?format=json"))
        if users["ocs"]["meta"]["statuscode"] != 200:
            raise Failure("Public URL administrator authentication failed")
        names = self.docker("container", "ls", "-a", "--format", "{{.Names}}").splitlines()
        if cfg.container in names:
            existing = json.loads(self.docker("container", "inspect", cfg.container))[0]
            if (existing["Config"].get("Labels") or {}).get(LABEL) != self.owner:
                raise Failure(f"Refusing to replace unowned container {cfg.container}; "
                              "remove the previous development installation explicitly first")
            self.existing = True
        listing = self.occ("app_api:app:list")
        self.registered = bool(re.search(r"^" + re.escape(self.app["appid"]) + r"\s+\(",
                                         listing, re.MULTILINE))
        if self.registered and not self.existing:
            raise Failure("A caption provider is already registered without an owned container; "
                          "unregister the previous development installation explicitly first")
        daemons = json.loads(self.occ("app_api:daemon:list", "--output=json"))
        for daemon in daemons:
            if daemon["name"] == cfg.daemon:
                if (daemon["deploy_id"] != "manual-install" or daemon["protocol"] != "http"
                        or daemon["host"] != cfg.container
                        or daemon["deploy_config"]["nextcloud_url"] != cfg.nextcloud_url):
                    raise Failure("Existing daemon does not match the supplied configuration")
                self.daemon_exists = True
        print("Preflight passed: configured harness is running and available.", flush=True)

    def deploy(self):
        cfg = self.cfg
        print(f"Building {cfg.image} from {ROOT}", flush=True)
        # Builds can download bundled models; the readiness timeout does not limit the build.
        self.run(["docker", "build", "-t", cfg.image, str(ROOT)],
                 timeout=cfg.build_timeout, stream=True)
        print("Registering development daemon and starting the container", flush=True)
        if not self.daemon_exists:
            self.occ("app_api:daemon:register", cfg.daemon, "Cassini Live development",
                     "manual-install", "http", cfg.container, cfg.nextcloud_url)
        if self.registered:
            self.occ("app_api:app:unregister", self.app["appid"], "--force")
        if self.existing:
            self.docker("rm", "-f", cfg.container)
        self.docker("volume", "create", cfg.volume)
        # New volumes are root-owned; the entrypoint drops to uid 10001 before the app starts.
        self.docker("run", "--rm", "--network", "none", "--user", "0",
                    "--mount", f"type=volume,src={cfg.volume},dst=/state",
                    "--entrypoint", "chown", cfg.image, "10001:10001", "/state")
        secret = cfg.app_secret or secrets.token_urlsafe(32)
        deploy_env = {
            "APP_HOST": "0.0.0.0", "APP_PORT": "8080", "APP_ID": self.app["appid"],
            "APP_VERSION": self.app["version"], "APP_SECRET": secret, "AA_VERSION": cfg.aa_version,
            "NEXTCLOUD_URL": cfg.nextcloud_url, "APP_PERSISTENT_STORAGE": "/state",
            "CASSINI_TALK_SIGNALING_INTERNAL_SECRET": cfg.hpb_secret,
            "CASSINI_TALK_HPB_URL": cfg.hpb_url,
        }
        args = ["run", "-d", "--name", cfg.container, "--network", cfg.network,
                "--label", f"{LABEL}={self.owner}", "--cpus", str(cfg.cpus),
                "--memory", cfg.memory, "--memory-swap", cfg.memory,
                "--pids-limit", str(cfg.pids_limit), "--log-driver", "local",
                "--log-opt", "max-size=10m", "--log-opt", "max-file=3",
                "--mount", f"type=volume,src={cfg.volume},dst=/state"]
        for key in deploy_env:
            args += ["-e", key]
        self.docker(*args, cfg.image, env={**os.environ, **deploy_env})
        deadline = time.monotonic() + cfg.timeout
        while True:
            try:
                self.docker("exec", cfg.nextcloud_container, "curl", "--fail", "--silent",
                            "--max-time", "5", f"http://{cfg.container}:8080/heartbeat")
                break
            except Failure:
                if time.monotonic() >= deadline:
                    raise Failure("Container heartbeat did not become ready") from None
                time.sleep(2)
        print("Registering ExApp and checking captions, HPB and UI routes", flush=True)
        registration = {**self.app, "daemon_config_name": cfg.daemon, "secret": secret,
                        "port": 8080, "protocol": "http", "system_app": 0}
        self.occ("app_api:app:register", self.app["appid"], cfg.daemon,
                 "--json-info", json.dumps(registration), "--force-scopes", "--wait-finish")
        self.docker("exec", cfg.container, "python", "-m", "cassini_live.capacity",
                    "--installed", "--wait", str(cfg.timeout), timeout=cfg.timeout + 15)
        self.docker("exec", "-i", cfg.container, "python", "-", input=HPB_PROBE)
        prefix = "/index.php/apps/app_api/proxy/" + self.app["appid"]
        for path in ("/panel/", "/settings/index.html"):
            self.page(prefix + path)
        print("Cassini Live is ready. Open these URLs using the exact hostname shown:")
        print(f"  Talk: {cfg.public_url}/index.php/apps/spreed/")
        print(f"  Transcript bookmarklet: {cfg.public_url}{prefix}/panel/")
        print(f"  Settings: {cfg.public_url}{prefix}/settings/index.html")
        print("Join a call with another participant and enable captions in the call menu.")


def main(argv=None):
    try:
        harness = Harness(config(argv))
        harness.preflight()
        if not harness.cfg.check_only:
            harness.deploy()
    except (Failure, OSError, ValueError, ET.ParseError, URLError) as exc:
        # Network errors can include URLs but never print subprocess output or credentials.
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
