"""Opt-in, bounded diagnostic publisher. No room content crosses this pipe."""

import asyncio
import json
import os
import sys
import time
from contextlib import suppress
from pathlib import Path

from .resources import snapshot


def process_usage(pid):
    # /proc stat's comm field may contain spaces and parentheses.
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    cpu = (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
    rss = int(fields[21]) * os.sysconf("SC_PAGE_SIZE")
    return cpu, rss


class DiagnosticVideo:
    """One publisher per installation; fail closed, without restart loops."""

    def __init__(self, service):
        self.service = service
        self.enabled = os.environ.get("CASSINI_LIVE_DEBUG_VIDEO", "").lower() in ("1", "true")
        self.task = None
        self.owner = None
        self.state = "idle" if self.enabled else "disabled"
        self.metrics = {}
        self._stop_task = None

    def start(self, token):
        if not self.enabled or self.task is not None:
            return
        self.owner = token
        self.task = asyncio.create_task(self._run(token))

    async def stop(self, token=None):
        if token is not None and token != self.owner:
            return
        if self.task is None:
            return
        if self._stop_task is None:
            self._stop_task = asyncio.create_task(self._stop(self.task))
        # Caller cancellation propagates, but reaping remains owned and tracked.
        await asyncio.shield(self._stop_task)

    async def _stop(self, task):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if self.task is task:
            self.task = None
            self.owner = None
            self.metrics = {}
            self.state = "idle" if self.enabled else "disabled"
        self._stop_task = None

    def status(self):
        return {**self.metrics, "enabled": self.enabled, "state": self.state}

    async def _run(self, token):
        process = None
        try:
            owner = self.service.clients.get(token)
            data = snapshot()
            safety = self.service.admission
            limit = data.get("memoryLimitBytes") or 0
            if (
                owner is None
                or safety.state != "ready"
                or not limit
                or data["memoryFraction"] + 128 * 1024**2 / limit >= 0.7
            ):
                self.state = "paused: insufficient diagnostic headroom"
                return
            self.state = "starting"
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "cassini_live.diagnostic_video",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env={**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"},
                limit=8192,
            )
            settings = self.service.settings
            config = {
                "room": token,
                "backend": os.environ.get("NEXTCLOUD_URL", ""),
                "server": settings.server,
                "stunservers": settings.model_dump()["stunservers"],
                "turnservers": settings.model_dump()["turnservers"],
            }
            process.stdin.write((json.dumps(config) + "\n").encode())
            await process.stdin.drain()
            booted = time.monotonic()
            previous_cpu, _ = process_usage(process.pid)
            previous_time = time.monotonic()
            previous_host = self._host_cpu()
            previous_caption = self._caption_cpu()
            high = 0
            while True:
                if self.service.clients.get(token) is not owner or owner.defunct.is_set():
                    self.state = "stopped: caption room closed"
                    return
                started = time.thread_time()
                data = snapshot()
                safety = self.service.admission
                if safety.state != "ready" or data["memoryFraction"] >= 0.7:
                    self.state = "paused: caption resource pressure"
                    return
                now = time.monotonic()
                elapsed = max(0.01, now - previous_time)
                cpu, rss = process_usage(process.pid)
                used = max(0, (cpu - previous_cpu) / elapsed)
                host = self._host_cpu()
                host_pct = 100 * (
                    1 - (host[1] - previous_host[1]) / max(1, host[0] - previous_host[0])
                )
                caption = self._caption_cpu()
                caption_pct = 100 * max(0, caption - previous_caption) / elapsed
                # Give module imports a short startup allowance. Thereafter the
                # entire child (rendering, codec, WebRTC) must fit 0.15 CPU.
                high = high + 1 if used > 0.15 and now - booted > 10 else 0
                if rss > 128 * 1024**2 or (high >= 3 and now - booted > 10):
                    self.state = "paused: diagnostic budget exceeded"
                    return
                pool = safety.pool
                busy = len(pool.leased)
                remaining = max(0, pool.admission_limit - busy)
                sample = {
                    "state": safety.state,
                    "rooms": len(self.service.clients),
                    "tracks": len(safety.tracks),
                    "maxTracks": safety.max_tracks,
                    "busy": busy,
                    "capacity": pool.admission_limit,
                    "spare": remaining,
                    "hostCpu": round(max(0, min(100, host_pct)), 1),
                    "hostAvailableGiB": round(data["hostAvailableBytes"] / 1024**3, 1),
                    "hostGiB": round(data["hostMemoryBytes"] / 1024**3, 1),
                    "captionCpu": round(caption_pct, 1),
                    "quota": data["cpuQuotaCpus"],
                    "captionMiB": round(
                        data["memoryFraction"] * data["memoryLimitBytes"] / 1024**2
                    ),
                    "limitMiB": round(data["memoryLimitBytes"] / 1024**2),
                    "drops": pool.dropped_chunks,
                    "waiting": pool.waiting,
                    "diagnosticCpu": round(used * 100, 2),
                    "diagnosticMiB": round(rss / 1024**2, 1),
                    "samplerMs": round((time.thread_time() - started) * 1000, 2),
                }
                process.stdin.write((json.dumps(sample) + "\n").encode())
                async with asyncio.timeout(8):
                    await process.stdin.drain()
                    reply = await process.stdout.readline()
                if not reply:
                    raise RuntimeError("Diagnostic publisher exited")
                report = json.loads(reply)
                self.metrics = {
                    k: sample[k] for k in ("diagnosticCpu", "diagnosticMiB", "samplerMs")
                }
                self.metrics.update(report)
                self.state = report.get("state", "publishing")
                if self.state in {"failed", "closed"} or (
                    now - booted > 30 and self.state != "connected"
                ):
                    return
                previous_cpu, previous_time = cpu, now
                previous_host, previous_caption = host, caption
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.state = "unavailable: " + type(error).__name__
        finally:
            if process and process.returncode is None:
                with suppress(ProcessLookupError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 3)
                except TimeoutError:
                    with suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()

    @staticmethod
    def _host_cpu():
        values = [int(x) for x in Path("/proc/stat").read_text().splitlines()[0].split()[1:9]]
        return sum(values), values[3] + values[4]

    @staticmethod
    def _caption_cpu():
        data = dict(
            line.split() for line in Path("/sys/fs/cgroup/cpu.stat").read_text().splitlines()
        )
        return int(data["usage_usec"]) / 1e6
