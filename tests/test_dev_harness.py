"""Standalone launcher contracts; no Docker daemon or application dependencies needed."""

import contextlib
import importlib.util
import io
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "dev_harness", Path(__file__).resolve().parents[1] / "scripts/dev_harness.py"
)
dev = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dev)
ENV = {"LIVE_DEV_HPB_URL": "http://host.docker.internal:28082", "LIVE_DEV_HPB_SECRET": "secret"}


class ConfigTests(unittest.TestCase):
    def test_every_option_supports_cli_over_env_over_default(self):
        cfg = dev.config([], ENV)
        self.assertEqual(cfg.memory, "2g")
        self.assertEqual(dev.config([], {**ENV, "LIVE_DEV_MEMORY": "3g"}).memory, "3g")
        for key in dev.DEFAULTS:
            attr = key.replace("-", "_")
            value = getattr(cfg, attr)
            if key == "app-secret":
                value = "explicit-secret"
            if isinstance(value, bool):
                value = str(value).lower()
            result = dev.config(["--" + key, str(value)],
                                {**ENV, "LIVE_DEV_" + attr.upper(): ""})
            self.assertEqual(str(getattr(result, attr)).lower(), str(value).lower())

    def test_missing_empty_and_invalid_configuration(self):
        cases = [{}, {**ENV, "LIVE_DEV_HPB_SECRET": ""},
                 {**ENV, "LIVE_DEV_APP_SECRET": ""}, {**ENV, "LIVE_DEV_CPUS": "nan"},
                 {**ENV, "LIVE_DEV_TIMEOUT": "0"}, {**ENV, "LIVE_DEV_MEMORY": "-1g"},
                 {**ENV, "LIVE_DEV_CHECK_ONLY": "yes"},
                 {**ENV, "LIVE_DEV_HPB_URL": "http://user:password@example.com"}]
        for env in cases:
            with self.subTest(env=list(env)), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    dev.config([], env)


class FakeHarness(dev.Harness):
    def __init__(self):
        super().__init__(dev.config([], ENV))
        self.calls = []
        self.present = False
        self.owned = False
        self.app_present = False
        self.stopped = False
        self.url = self.cfg.public_url
        self.daemons = []
        self.enabled = {"spreed": "x", "app_api": "x"}

    def docker(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if args[:2] == ("container", "inspect"):
            return json.dumps([{"State": {"Running": not self.stopped},
                                "NetworkSettings": {"Networks": {self.cfg.network: {}}},
                                "Config": {"Labels": {
                                    dev.LABEL: self.owner if self.owned else ""}}}])
        if args[:2] == ("container", "ls"):
            return self.cfg.container if self.present else ""
        return ""

    def occ(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if args[0] == "status":
            return json.dumps({"installed": True, "maintenance": False})
        if args[0] == "app:list":
            return json.dumps({"enabled": self.enabled})
        if args[0] == "config:system:get":
            return self.url
        if args[0] == "app_api:app:list":
            return "ExApps:\nlive_transcription (Cassini Live): 0.10.0 [enabled]" \
                if self.app_present else "ExApps:"
        if args[0] == "app_api:daemon:list":
            return json.dumps(self.daemons)
        return ""

    def page(self, path):
        return b'{"ocs":{"meta":{"statuscode":200}}}'

    def run(self, args, **kwargs):
        self.calls.append((tuple(args), kwargs))
        return ""


class LifecycleTests(unittest.TestCase):
    def test_preflight_does_not_mutate(self):
        harness = FakeHarness()
        harness.preflight()
        self.assertFalse(any(call[0][0] in {"run", "rm", "volume", "docker"}
                             for call in harness.calls))

    def test_missing_prerequisites_and_foreign_resources_fail(self):
        for field, value in (("stopped", True), ("url", "http://127.0.0.1:28080"),
                             ("enabled", {"spreed": "x"}), ("present", True),
                             ("app_present", True)):
            harness = FakeHarness()
            setattr(harness, field, value)
            with self.subTest(field=field), self.assertRaises(dev.Failure):
                harness.preflight()

    def test_conflicting_daemon_fails(self):
        harness = FakeHarness()
        harness.daemons = [{"name": harness.cfg.daemon, "deploy_id": "docker-install"}]
        with self.assertRaises(dev.Failure):
            harness.preflight()

    def test_owned_rerun_builds_before_removal_and_preserves_volume(self):
        harness = FakeHarness()
        harness.present = harness.owned = harness.app_present = True
        harness.preflight()
        harness.deploy()
        args = [call[0] for call in harness.calls]
        build = next(i for i, a in enumerate(args) if a[:2] == ("docker", "build"))
        unregister = next(i for i, a in enumerate(args) if a[0] == "app_api:app:unregister")
        remove = next(i for i, a in enumerate(args) if a[:2] == ("rm", "-f"))
        self.assertLess(build, unregister)
        self.assertLess(unregister, remove)
        self.assertFalse(any(a[:2] == ("volume", "rm") for a in args))
        run, kwargs = next((a, k) for a, k in harness.calls if a[:2] == ("run", "-d"))
        self.assertEqual(run[run.index("--memory") + 1], "2g")
        self.assertEqual(run[run.index("--memory-swap") + 1], "2g")
        self.assertIn("type=volume,src=cassini_live_dev_data,dst=/state", run)
        self.assertNotIn("secret", run)
        self.assertEqual(kwargs["env"]["CASSINI_TALK_SIGNALING_INTERNAL_SECRET"], "secret")
        register = next(a for a in args if a[0] == "app_api:app:register")
        info = json.loads(register[register.index("--json-info") + 1])
        self.assertEqual(info["secret"], kwargs["env"]["APP_SECRET"])
        self.assertEqual(info["routes"], dev.manifest()["routes"])
        self.assertTrue(any(k.get("input") == dev.HPB_PROBE for _, k in harness.calls))

    def test_build_failure_leaves_existing_deployment_untouched(self):
        harness = FakeHarness()
        harness.present = harness.owned = harness.app_present = True
        harness.preflight()
        with patch.object(harness, "run", side_effect=dev.Failure("build failed")):
            with self.assertRaises(dev.Failure):
                harness.deploy()
        self.assertFalse(any(a[0] in {"rm", "app_api:app:unregister"}
                             for a, _ in harness.calls))

    def test_heartbeat_timeout_stops_before_registration(self):
        harness = FakeHarness()
        docker = harness.docker

        def fail_heartbeat(*args, **kwargs):
            if args[:3] == ("exec", harness.cfg.nextcloud_container, "curl"):
                raise dev.Failure("unreachable")
            return docker(*args, **kwargs)

        with patch.object(harness, "docker", side_effect=fail_heartbeat), \
                patch.object(dev.time, "monotonic", side_effect=[0, 181]):
            with self.assertRaisesRegex(dev.Failure, "heartbeat"):
                harness.deploy()
        self.assertFalse(any(a[0] == "app_api:app:register" for a, _ in harness.calls))

    def test_failure_does_not_print_credentials(self):
        harness = dev.Harness(dev.config([], ENV))
        with patch.object(dev.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 1, stdout="secret", stderr="secret")):
            with self.assertRaises(dev.Failure) as caught:
                harness.run(["docker", "secret"])
        self.assertNotIn("secret", str(caught.exception))

    def test_check_only_never_deploys(self):
        with patch.object(dev.Harness, "preflight") as preflight, \
                patch.object(dev.Harness, "deploy") as deploy, \
                patch.dict(dev.os.environ, ENV, clear=True):
            self.assertEqual(dev.main(["--check-only"]), 0)
            preflight.assert_called_once()
            deploy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
