"""Opt-in, bounded diagnostic publisher. No room content crosses this pipe."""

import asyncio
import json
import logging
import os
import sys
import time
import weakref
from contextlib import suppress
from pathlib import Path

from .resources import snapshot

LOGGER = logging.getLogger(__name__)
# One sample per second, matching the governor's tick and the publisher's 1 fps.
SAMPLE_S = 1.0
# Stop the child after 6 s above 15 % of one CPU (three 2 s samples before 0.7.4).
HIGH_SAMPLES = 6
VERBS = {None: "admit", "shed": "keep paused"}


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
        self._stop_reason = "stopped"
        # Anonymous, process-local lane numbers; never session or participant ids.
        self._lanes = weakref.WeakKeyDictionary()
        self._next_lane = 0
        self._scribes = {}

    def _lane(self, transcriber):
        if transcriber not in self._lanes:
            self._next_lane += 1
            self._lanes[transcriber] = self._next_lane
        return self._lanes[transcriber]

    def _scribe(self, pool, worker):
        if worker is None:
            return 0
        for known in [w for w in self._scribes if w not in pool.workers]:
            del self._scribes[known]
        if worker not in self._scribes:
            used = set(self._scribes.values())
            self._scribes[worker] = next(n for n in range(1, len(used) + 2) if n not in used)
        return self._scribes[worker]

    def _lane_sample(self, pool, transcriber, name=None, other=False, now=None):
        """One lane. `name` only for the room that owns the video; other rooms' lanes are
        marked `other` and carry no name."""
        gate = transcriber.gate
        held = getattr(transcriber, "worker", None)
        active = bool(getattr(gate, "active", False))
        queued = transcriber.queue.qsize() if hasattr(transcriber, "queue") else 0
        now = time.monotonic() if now is None else now
        paused_until = getattr(transcriber, "paused_until", None)
        sample = {
            "id": self._lane(transcriber),
            "active": active,
            "speech": getattr(gate, "chunks_emitted", 0),
            "lost": getattr(transcriber, "lost_chunks", 0),
            "scribe": self._scribe(pool, held),
            "waiting": held is None and queued > 0 and bool(pool.waiting),
            "paused": paused_until is not None and paused_until > now,
            "lag": self._lane_lag(transcriber, now),
        }
        if other:
            sample["other"] = True
        elif name:
            sample["name"] = name
        return sample

    @staticmethod
    def _lane_lag(transcriber, now):
        """Seconds this speaker's oldest undecoded audio waits: from the lease while
        recognising (the governor's control lag), from the chunk while held."""
        pending = getattr(transcriber, "pending_since", None)
        if pending is None:
            return 0.0 if getattr(transcriber, "worker", None) is not None else None
        has_lease = getattr(transcriber, "has_lease", None)
        if has_lease is not None and has_lease():
            lease_at = getattr(transcriber, "lease_at", None) or pending
            return round(max(0.0, now - max(pending, lease_at)), 2)
        return round(max(0.0, now - pending), 2)

    def _who(self, ref, owner, labels):
        transcriber = ref() if ref is not None else None
        if transcriber is None:
            return "a speaker"
        if transcriber in labels:
            return labels[transcriber]
        return "someone in another call"

    def _decision(self, governor, owner, labels):
        """The governor's latest decision or shed, in words, naming only this room."""
        thresholds = governor.thresholds
        decision, shed = governor.last_decision, governor.last_shed
        if shed and (not decision or shed["at"] >= decision["at"]):
            return {
                "at": shed["at"],
                "verb": "pause",
                "who": self._who(governor.last_shed_ref, owner, labels),
                "text": f"lag {shed['lag']:.1f} s ≥ shed {thresholds.lag_shed:.1f} for 5 s"
                " · newest seat",
            }
        if not decision:
            return None
        reason, lag = decision["reason"], decision["lag"]
        stall, headroom = decision["cpuStall"], decision["headroomMiB"]
        if reason is None:
            text = f"seat {decision['seats']} of {decision['ceiling']} · lag {lag:.1f} s"
            text += f" < {thresholds.lag_admit:.1f}"
            if stall is not None:
                text += f" · stall {stall * 100:.0f}%"
            if headroom is not None:
                text += f" · {headroom / 1024:.1f} GiB free"
        elif reason == "lag":
            text = (
                f"lag {lag:.1f} s ≥ admit {thresholds.lag_admit:.1f}"
                f" · reopens under {thresholds.lag_admit / 2:.1f} s for 5 s"
            )
        elif reason == "cpu":
            text = f"CPU stall {(stall or 0) * 100:.0f}% ≥ {thresholds.cpu_stall_admit * 100:.0f}%"
        elif reason == "memory":
            text = f"{headroom if headroom is not None else '?'} MiB free, under two speakers' need"
        elif reason == "ceiling":
            text = f"all {decision['ceiling']} seats taken"
        else:
            text = "held off after a pause"
        return {
            "at": decision["at"],
            "verb": VERBS.get(reason, "hold"),
            "who": self._who(governor.last_decision_ref, owner, labels),
            "text": text,
        }

    @staticmethod
    def _speakers(owner, clients):
        """Every live transcriber with its room, and display labels for the owning room's
        speakers only. Names cross the pipe for that room alone and are never logged."""
        speakers = [
            (c, sid, t) for c in clients for sid, t in list(getattr(c, "transcribers", {}).items())
        ]
        name = getattr(owner, "speaker_name", lambda _sid: None)
        labels = {
            # 40 characters bound the line the child reads (8 KiB pipe limit).
            t: (name(sid) or "Participant")[:40]
            for c, sid, t in speakers
            if c is owner
        }
        return speakers, labels

    def _lane_samples(self, pool, owner, speakers, labels, now):
        # Ten lanes at most; the viewers' own room comes first.
        ordered = sorted(speakers, key=lambda speaker: speaker[0] is not owner)
        return [
            self._lane_sample(pool, t, labels.get(t), c is not owner, now)
            for c, _, t in ordered[:10]
        ]

    @staticmethod
    def _cpu_weight():
        try:
            return int(Path("/sys/fs/cgroup/cpu.weight").read_text())
        except (OSError, ValueError):
            return None

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
            self._stop_reason = (
                "stopped: caption room closed" if token else "stopped: caption service closed rooms"
            )
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
                "backend": owner.backend_url,
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
            weight = self._cpu_weight()
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
                if rss > 128 * 1024**2 or (high >= HIGH_SAMPLES and now - booted > 10):
                    self.state = "paused: diagnostic budget exceeded"
                    return
                pool = safety.pool
                busy = pool.busy
                remaining = max(0, pool.admission_limit - busy)
                clients = list(self.service.clients.values())
                speakers, labels = self._speakers(owner, clients)
                transcribers = [t for _, _, t in speakers]
                governor = getattr(safety, "governor", None)
                admission = governor.status() if governor is not None else {}
                if governor is not None:
                    thresholds = governor.thresholds
                    admission.update(
                        lagAdmit=thresholds.lag_admit,
                        lagShed=thresholds.lag_shed,
                        cpuStallAdmit=thresholds.cpu_stall_admit,
                        holdOff=thresholds.hold_off,
                    )
                lag_meter = getattr(pool, "lag", None)
                ends = getattr(pool, "stream_ends", {})
                sample = {
                    "engine": getattr(pool, "engine_id", "whistle"),
                    "state": safety.state,
                    "rooms": len(clients),
                    "tracks": len(safety.tracks),
                    "maxTracks": safety.max_tracks,
                    "busy": busy,
                    "capacity": pool.admission_limit,
                    "spare": remaining,
                    "vadActive": sum(1 for t in transcribers if getattr(t.gate, "active", False)),
                    "vadPeak": round(
                        max(
                            (getattr(t.gate, "last_probability", 0.0) for t in transcribers),
                            default=0.0,
                        ),
                        2,
                    ),
                    "vadChunks": sum(getattr(t.gate, "chunks_emitted", 0) for t in transcribers),
                    "queuedChunks": sum(
                        t.queue.qsize() for t in transcribers if hasattr(t, "queue")
                    ),
                    "sttChunks": getattr(pool, "chunks_processed", 0),
                    "sttAudioSec": round(getattr(pool, "audio_seconds", 0.0), 1),
                    "leasedSec": round(
                        sum(getattr(t, "lease_samples", 0) for t in transcribers) / 16000, 1
                    ),
                    "yields": getattr(pool, "scheduling_yields", 0),
                    "endSilence": ends.get("silence", 0),
                    "endDuration": ends.get("durationPause", 0) + ends.get("durationLimit", 0),
                    "endSched": ends.get("schedulingPause", 0) + ends.get("schedulingLimit", 0),
                    "endFlush": ends.get("flush", 0),
                    "targets": sum(len(getattr(c, "targets", {})) for c in clients),
                    "previewing": sum(1 for t in transcribers if getattr(t, "pending", "")),
                    "finals": getattr(pool, "finals_emitted", 0),
                    "partials": getattr(pool, "partials_emitted", 0),
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
                    "lanes": self._lane_samples(pool, owner, speakers, labels, now),
                    "admission": admission,
                    "decision": (
                        self._decision(governor, owner, labels) if governor is not None else None
                    ),
                    "p95": lag_meter.p95() if lag_meter is not None else None,
                    "threads": getattr(pool, "threads", 1),
                    "segmentSeconds": max(
                        (getattr(t, "SEGMENT_SECONDS", 0) for t in transcribers), default=None
                    ),
                    "cpuWeight": weight,
                    "oomKills": data.get("oomKills", 0),
                    "engineRestarts": getattr(pool, "restarts", getattr(pool, "failures", 0)),
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
                await asyncio.sleep(SAMPLE_S)
        except asyncio.CancelledError:
            self.state = self._stop_reason
            raise
        except Exception as error:
            self.state = "unavailable: " + type(error).__name__
        finally:
            LOGGER.info("Diagnostic video ended (%s)", self.state)
            if process and process.returncode is None:
                if process.stdin and hasattr(process.stdin, "close"):
                    with suppress(Exception):
                        process.stdin.close()
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
