"""Capacity measurement an administrator asks for; also runnable without Nextcloud.

Since 0.8.0 nothing runs it automatically and its result is displayed, never used as a
gate: admission decides per speaker on live load (load.py)."""

import argparse
import asyncio
import base64
import http.client
import json
import logging
import os
import socket
import sys
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from . import __version__, catalog, engines
from .calibration import scenario
from .config import Config
from .resources import limits

LOGGER = logging.getLogger(__name__)


class CapacityAssessment:
    # The run's own bound (asyncio.timeout in run()): four 21 s scenarios plus margin.
    BUDGET_S = 120

    def __init__(self, pool):
        self.pool = pool
        self.task = None
        self.interruption_lock = asyncio.Lock()
        self.result = {"state": "not_tested", "testedSpeakers": 0}
        self.started_at = None

    def start(self):
        if self.task and not self.task.done():
            return
        self.result = {"state": "checking", "testedSpeakers": 0}
        self.started_at = time.monotonic()
        self.task = asyncio.create_task(self.run())

    def remaining(self):
        """Seconds the running measurement may still hold the service (Retry-After)."""
        if self.task is None or self.task.done() or self.started_at is None:
            return 0
        return max(0, self.BUDGET_S - (time.monotonic() - self.started_at))

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
        requests = list(pool.lag.requests) if hasattr(pool, "lag") else None
        report = {
            "state": "checking",
            "testedSpeakers": 0,
            "testedParticipants": 10,
            "configuredWorkers": pool.size,
            "engine": pool.engine_id,
            "appVersion": __version__,
            "limits": limits(),
            "scenarios": [],
            "scope": "Short English recording and synthetic noise; no network or microphone test",
        }
        self.result = report
        try:
            if not pool.is_idle():
                raise RuntimeError("Capacity check requires an idle service")
            async with asyncio.timeout(self.BUDGET_S):
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
            if requests is not None:
                # Synthetic request latencies must not stand in for calls' p95 either.
                pool.lag.requests.clear()
                pool.lag.requests.extend(requests)


class ServiceRefused(RuntimeError):
    """The running service answered with an error status; detail is its reason."""

    def __init__(self, method, path, status, detail):
        super().__init__(
            f"{method} {path} returned HTTP {status}" + (f": {detail}" if detail else "")
        )
        self.status, self.detail = status, detail


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
            try:
                detail = json.loads(response.read()).get("detail")
            except (ValueError, AttributeError):
                detail = None
            raise ServiceRefused(method, path, response.status, detail)
        return json.loads(response.read())
    finally:
        connection.close()


WATCH_INTERVAL_S = 1.0  # the safety monitor's tick; a faster read would repeat samples


# Read failures the service recovers from on its own: a restarting container (refused
# or reset connection, socket timeout), an error page or a body cut off mid-write.
READ_FAILURES = (OSError, http.client.HTTPException, RuntimeError, ValueError)


def watch_line(status, error=None):
    """One per-second record of the lag signals (0.8.0-instant-admission.md, staging
    validation plan 1) from a /status body: the governor's admission block plus the
    backend counters that show when the backlog turns into drops. A failed read keeps
    the columns (all None) and names the error, so the file stays one row per second."""
    safety = status.get("resourceSafety") or {}
    admission = safety.get("admission") or {}
    return {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "state": safety.get("state"),
        **{
            key: admission.get(key)
            for key in (
                "lagSeconds",
                "lagSustainedSeconds",
                "captionAgeSeconds",
                "cpuStall",
                "cpuPressureAvg10",
                "engineDuty",
                "memoryUsedMiB",
                "memoryHeadroomMiB",
            )
        },
        "p95RequestSeconds": status.get("p95RequestSeconds"),
        "droppedSpeechChunks": status.get("droppedSpeechChunks"),
        "capacityWaits": status.get("capacityWaits"),
        "pausedDrops": status.get("pausedDrops"),
        "hostRestarts": status.get("hostRestarts"),
        **{
            key: admission.get(key)
            for key in ("seats", "speaking", "lastRefusal", "lastShed", "tickMs")
        },
        # The whole monitor tick (snapshot, duty sample and governor), the figure the
        # design's 5 ms budget is about; tickMs above is the governor alone.
        "monitorTickMs": (safety.get("cpuHealth") or {}).get("monitorTickMs"),
        **({"error": error} if error else {}),
    }


async def watch(seconds):
    """Print one JSON line per second from the running service; exit 0 iff still ready.
    A read the service fails (restarting after a trip, timing out, truncating the body)
    prints an error line and the run goes on: the seconds after a trip are the ones the
    threshold derivation needs."""
    next_at = time.monotonic()
    ready = False
    for remaining in range(max(1, seconds), 0, -1):
        try:
            line = watch_line(installed_request("GET", "/status"))
        except READ_FAILURES as error:
            line = watch_line({}, error=f"{type(error).__name__}: {error}")
        ready = line["state"] == "ready"
        print(json.dumps(line), flush=True)
        if remaining > 1:
            # A read that outlasted the interval (the 15 s socket timeout) re-anchors
            # instead of firing the missed reads back to back on one second.
            next_at = max(next_at + WATCH_INTERVAL_S, time.monotonic())
            await asyncio.sleep(max(0, next_at - time.monotonic()))
    return 0 if ready else 1


async def measure_installed():
    """Ask the running service for a measurement, then wait for its result. Exit 0 iff
    at least one speaker passed."""
    try:
        installed_request("POST", "/capacity/check")
    except ServiceRefused as refused:
        if refused.status != 409:
            raise
        # Calls in progress, a switch, captions not ready or a run already going.
        print(
            f"Capacity measurement not started: {refused.detail or 'the service is busy'}",
            file=sys.stderr,
        )
        return 1
    started = time.monotonic()
    while True:
        status = installed_request("GET", "/status")
        result = {**status["capacityAssessment"], "resourceSafety": status["resourceSafety"]}
        if result["state"] != "checking":
            break
        if time.monotonic() - started >= CapacityAssessment.BUDGET_S + 30:
            break  # Report what is there rather than a timeout traceback.
        await asyncio.sleep(1)
    print(json.dumps(result, indent=2))
    return 0 if result["state"] in {"passed", "limited"} else 1


async def main(args):
    if args.watch:
        return await watch(args.watch)
    if args.installed and args.measure:
        return await measure_installed()
    if args.installed:
        started = time.monotonic()
        while True:
            status = installed_request("GET", "/status")
            safety = status["resourceSafety"]
            result = {**status["capacityAssessment"], "resourceSafety": safety}
            if safety["state"] in {"ready", "blocked", "disabled"}:
                break
            if time.monotonic() - started >= args.wait:
                break  # Report current recovery state rather than a timeout traceback.
            await asyncio.sleep(1)
    else:
        config = Config.from_env()
        size = args.workers or (
            config.workers
            if config.workers_explicit or args.engine == catalog.DEFAULT_ENGINE
            else catalog.worker_ceiling(args.engine, limits()["effectiveCpus"])
        )
        pool = engines.create_backend(
            args.engine,
            size=size,
            weights=args.weights or config.weights,
            model_dir=args.model_dir,
            threads=catalog.engine_threads(limits()["effectiveCpus"]),
        )
        from .safety import SafetyController

        assessment = CapacityAssessment(pool)
        controller = SafetyController(pool, assessment)
        try:
            await controller.start()
            if controller.state == "ready":
                await controller.measure()
                await assessment.task
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
    parser.add_argument(
        "--measure",
        action="store_true",
        help="With --installed: measure the running service's capacity (no calls may be "
        "captioned; captions pause for up to two minutes)",
    )
    parser.add_argument(
        "--watch",
        type=int,
        default=0,
        metavar="N",
        help="Print one JSON line per second of the running service's lag signals for N "
        "seconds (from the container, like --installed)",
    )
    parser.add_argument("--workers", type=int, choices=range(1, 33))
    parser.add_argument("--weights")
    parser.add_argument(
        "--engine",
        choices=list(catalog.ENGINES),
        default=catalog.DEFAULT_ENGINE,
        help="Speech engine to test. A Nemotron run loads about 1 GB: start it under "
        "systemd-run --user --scope -p MemoryMax=3G -p MemorySwapMax=0",
    )
    parser.add_argument("--model-dir", type=Path, help="Verified Nemotron model directory")
    raise SystemExit(asyncio.run(main(parser.parse_args())))
