"""Installation capacity assessment; also runnable without Nextcloud or a call."""

import argparse
import asyncio
import base64
import http.client
import json
import logging
import os
import socket
import time
from contextlib import suppress
from datetime import UTC, datetime

from . import __version__
from .calibration import scenario
from .config import Config
from .pool import WorkerPool
from .resources import limits

LOGGER = logging.getLogger(__name__)


class CapacityAssessment:
    def __init__(self, pool):
        self.pool = pool
        self.task = None
        self.interruption_lock = asyncio.Lock()
        self.result = {"state": "not_tested", "testedSpeakers": 0}

    def start(self):
        if self.task and not self.task.done():
            return
        self.result = {"state": "checking", "testedSpeakers": 0}
        self.task = asyncio.create_task(self.run())

    async def interrupt(self):
        async with self.interruption_lock:
            if self.task and not self.task.done():
                self.task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.task

    async def run(self):
        pool = self.pool
        counters = (
            pool.failures,
            pool.capacity_waits,
            pool.dropped_chunks,
            pool.scheduling_yields,
            pool.last_pressure,
        )
        report = {
            "state": "checking",
            "testedSpeakers": 0,
            "testedParticipants": 10,
            "configuredWorkers": pool.size,
            "appVersion": __version__,
            "limits": limits(),
            "scenarios": [],
            "scope": "Short English recording and synthetic noise; no network or microphone test",
        }
        self.result = report
        try:
            if pool.idle.qsize() != pool.size:
                raise RuntimeError("Capacity check requires an idle service")
            async with asyncio.timeout(120):
                for count in range(1, min(pool.size, 4) + 1):
                    result = await scenario(pool, count)
                    report["scenarios"].append(result)
                    if not result["passed"]:
                        break
                    report["testedSpeakers"] = count
            passed = report["testedSpeakers"]
            report["state"] = (
                "passed"
                if passed == min(pool.size, 4)
                else ("limited" if passed else "insufficient")
            )
            report["checkedAt"] = datetime.now(UTC).isoformat()
        except asyncio.CancelledError:
            report["state"] = "interrupted"
            report["testedSpeakers"] = 0
            raise
        except Exception as error:
            report["state"] = "error"
            report["testedSpeakers"] = 0
            report["error"] = type(error).__name__
            LOGGER.warning("Capacity assessment did not complete (%s)", type(error).__name__)
        finally:
            # The service excludes calls during this workload. Simulated failures
            # must not contaminate production call health metrics.
            (
                pool.failures,
                pool.capacity_waits,
                pool.dropped_chunks,
                pool.scheduling_yields,
                pool.last_pressure,
            ) = counters


def installed_request(method, path):
    connection = http.client.HTTPConnection(
        "127.0.0.1", int(os.environ.get("APP_PORT", "8080")), timeout=15
    )
    if os.environ.get("HP_SHARED_KEY"):
        connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.sock.settimeout(15)
        connection.sock.connect(os.environ.get("HP_EXAPP_SOCK", "/tmp/exapp.sock"))
    try:
        connection.request(
            method,
            path,
            headers={
                "EX-APP-ID": os.environ["APP_ID"],
                "EX-APP-VERSION": os.environ["APP_VERSION"],
                "AUTHORIZATION-APP-API": base64.b64encode(
                    (":" + os.environ["APP_SECRET"]).encode()
                ).decode(),
            },
        )
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError(
                f"Capacity check HTTP {response.status}; check that no calls are active"
            )
        return json.loads(response.read())
    finally:
        connection.close()


async def main(args):
    if args.installed:
        started = time.monotonic()
        while True:
            status = installed_request("GET", "/status")
            safety = status["resourceSafety"]
            result = {**status["capacityAssessment"], "resourceSafety": safety}
            state = safety["state"]
            budget = max(150, args.wait) if state == "checking" else args.wait
            if state in {"ready", "blocked", "disabled"}:
                break
            if time.monotonic() - started >= budget:
                break  # Report current recovery state rather than a timeout traceback.
            await asyncio.sleep(1)
    else:
        config = Config.from_env()
        pool = WorkerPool(args.workers or config.workers, args.weights or config.weights)
        from .safety import SafetyController

        assessment = CapacityAssessment(pool)
        controller = SafetyController(pool, assessment)
        try:
            await controller.start()
            while controller.state == "checking":
                await asyncio.sleep(0.1)
            result = {**assessment.result, "resourceSafety": controller.status()}
        finally:
            await controller.close()
    print(json.dumps(result, indent=2))
    return 0 if result["resourceSafety"]["state"] == "ready" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--installed",
        action="store_true",
        help="Report the running service capacity and automatic recovery status",
    )
    parser.add_argument(
        "--wait", type=int, default=0, help="Seconds to wait through installed recovery"
    )
    parser.add_argument("--workers", type=int, choices=range(1, 33))
    parser.add_argument("--weights")
    raise SystemExit(asyncio.run(main(parser.parse_args())))
