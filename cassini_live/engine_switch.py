"""Server-wide speech engine selection: restore, switch, roll back.

One engine serves every call. A switch downloads while the old engine keeps serving, then
stops captions, loads the new engine and is done once it is ready: no capacity check runs
(0.8.0). A failed load restores the previous engine, so an administrator's choice can
never leave the server without captions longer than two loads.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Literal

from . import catalog, eligibility, engines
from .config import LANGUAGE_NAMES, LANGUAGES
from .eligibility import NetworkProbe, Reason
from .model_store import ModelError, ModelStore
from .safety import ResourceRefused
from .settings import EngineSettings, SettingsStore

LOGGER = logging.getLogger(__name__)

SwitchState = Literal[
    "idle", "validating", "downloading", "verifying", "stopping", "loading",
    "rolling_back", "succeeded", "failed", "cancelled",
]
ACTIVE_STATES = {"validating", "downloading", "verifying", "stopping", "loading", "rolling_back"}
ENDED_STATES = {"succeeded", "failed", "cancelled"}
CRITICAL_STATES = {"stopping", "loading", "rolling_back"}
# Error codes 0.7.x saved that 0.8.0 no longer produces; the form still renders them.
LEGACY_CODES = {"capacity_failed"}
# Bound on stop_rooms during a switch's teardown (safety._teardown), added to the load bound.
STOP_ROOMS_S = 30
REMOVABLE = {"ready", "partial", "corrupt"}
# Present model files make these irrelevant when restoring a saved choice.
DOWNLOAD_REASONS = {"network", "disk_space", "storage"}
FACTS_MAX_AGE = 5.0
CPU_FLAGS = {flag for e in catalog.ENGINES.values() for flag in e.requirements.cpu_flags}


@dataclass
class SwitchStatus:
    state: SwitchState = "idle"
    target: str | None = None
    previous: str | None = None
    started_at: float | None = None
    updated_at: float | None = None
    message: str = ""
    error: dict | None = None
    rolled_back: bool = False

    def to_json(self) -> dict:
        return {
            "state": self.state,
            "target": self.target,
            "previous": self.previous,
            "startedAt": self.started_at,
            "updatedAt": self.updated_at,
            "message": self.message,
            "error": self.error,
            "rolledBack": self.rolled_back,
        }


class SwitchRefused(Exception):
    def __init__(self, status: int, code: str, message: str, reasons: tuple[Reason, ...] = ()):
        super().__init__(message)
        self.status, self.code, self.reasons = status, code, reasons


class _Interrupted(Exception):
    """An administrator cancelled the current step (not a task cancellation)."""


def _name(engine_id):
    return catalog.get(engine_id).name if engine_id in catalog.ENGINES else str(engine_id)


WAITING_PHASES = frozenset({"cooling_down"})


def _failure_code(reason):
    if "did not load in time" in reason:
        return "timeout"
    return "load_failed"


class EngineManager:
    def __init__(self, safety, service, config, store: ModelStore, settings: SettingsStore, *,
                 backend_factory=engines.create_backend, facts=eligibility.host_facts,
                 probe: NetworkProbe | None = None, clock=time.time,
                 check_timeout: float | None = None):
        self.safety, self.service, self.config = safety, service, config
        self.store, self.settings = store, settings
        self.backend_factory, self.facts_source, self.probe = backend_factory, facts, probe
        # None: each engine's own load bound (start_timeout_s) plus the room teardown bound.
        self.clock, self.check_timeout = clock, check_timeout
        self.lock = asyncio.Lock()
        self.active = safety.engine_id
        self.status = SwitchStatus()
        self.on_change: Callable[[], None] | None = None
        self.last_refusal: dict | None = None
        self.task: asyncio.Task | None = None
        self.step: asyncio.Task | None = None
        self.watch: asyncio.Task | None = None
        self.restore_watch = False
        self.cancel_reason: str | None = None
        # Calls whose captions are being set up; the app keeps the count.
        self.calls_starting: Callable[[], int] = lambda: 0
        safety.on_measured = self.notify
        self._facts = None
        try:
            self.saved = settings.read()
        except ValueError:
            self.saved = EngineSettings()

    @property
    def busy(self) -> bool:
        return self.status.state in ACTIVE_STATES

    @property
    def critical(self) -> bool:
        return self.status.state in CRITICAL_STATES

    @property
    def desired(self) -> str:
        """What the settings form shows: the change in progress, else the admin's choice.
        The choice differs from the active engine only while a restore is deferred."""
        if self.busy and self.status.target:
            return self.status.target
        saved = getattr(self.saved, "engine", None)
        return saved if saved in catalog.ENGINES else self.active

    @property
    def deferred(self) -> str | None:
        """The admin's choice while a restore waits for suitable limits, else None."""
        engine = self.desired
        return engine if not self.busy and engine != self.active else None

    # Notifications and persistence

    def notify(self):
        if self.on_change is None:
            return
        try:
            self.on_change()
        except Exception:
            LOGGER.exception("Engine change notification failed")

    def _save(self, **changes):
        for key, value in changes.items():
            setattr(self.saved, key, value)
        self.saved.updated_at = self.clock()
        try:
            self.settings.write(self.saved)
        except (OSError, ValueError) as error:
            LOGGER.warning("Could not persist engine settings (%s)", type(error).__name__)
        self.notify()

    def _error(self, code, message, engine):
        return {"code": code, "message": message, "engine": engine, "at": round(self.clock())}

    def _set(self, state, message="", **changes):
        if state in ENDED_STATES:
            # A refusal noted during the change is older than its result; keeping it would
            # put "Not applied" next to "Now using".
            self.last_refusal = None
        self.status.state, self.status.message = state, message
        self.status.updated_at = self.clock()
        for key, value in changes.items():
            setattr(self.status, key, value)
        self.notify()

    # Host facts and eligibility

    def _host_facts(self, fresh=False):
        now = time.monotonic()
        if fresh or self._facts is None or now - self._facts[0] > FACTS_MAX_AGE:
            facts = self.facts_source(
                reader=self.safety.reader, store_root=self.store.root, probe=self.probe
            )
            self._facts = (now, facts)
        return self._facts[1]

    def _assess(self, engine_id, facts):
        return eligibility.assess(catalog.get(engine_id), facts, self.store.state(engine_id))

    def _cpus(self):
        try:
            return self.safety.reader()["effectiveCpus"]
        except Exception:
            return 1.0

    def _size(self, engine_id):
        # With a shared host, CASSINI_LIVE_WORKERS is the speaker ceiling (docs/capacity.md).
        if self.config.workers_explicit:
            return self.config.workers
        return catalog.worker_ceiling(engine_id, self._cpus())

    def _backend(self, engine_id):
        engine = catalog.get(engine_id)
        size = self._size(engine_id)
        if engine.kind == "worker_pool":
            return self.backend_factory(engine_id, size=size, weights=self.config.weights)
        return self.backend_factory(
            engine_id,
            size=size,
            weights=self.config.weights,
            model_dir=self.store.model_dir(engine_id),
            threads=catalog.engine_threads(self._cpus()),
        )

    # Lifecycle

    async def restore(self) -> None:
        async with self.lock:
            try:
                saved = self.settings.read()
            except ValueError:
                saved = EngineSettings(last_error=self._error(
                    "settings", "Saved engine settings were unreadable; using Whistle", "whistle"
                ))
            if saved.pending:
                saved.last_error = self._error(
                    "interrupted",
                    f"The change to {_name(saved.pending)} was interrupted by a restart",
                    saved.pending,
                )
                saved.pending = None
            engine = saved.engine
            if engine != catalog.DEFAULT_ENGINE:
                if problem := await self._restore_problem(engine):
                    host_limits, message = problem
                    if host_limits:
                        # AppAPI starts each new container before the hardening step sets its
                        # limits: keep the choice and restore it at the next suitable start.
                        saved.last_error = self._error(
                            "restore_deferred",
                            f"{message} {_name(engine)} stays selected and returns when Cassini "
                            "Live restarts with suitable limits.",
                            engine,
                        )
                    else:
                        saved.engine = catalog.DEFAULT_ENGINE
                        saved.last_error = self._error(
                            "restore_failed", f"{message} Choose it again to download it.", engine
                        )
                    engine = catalog.DEFAULT_ENGINE
                elif (saved.last_error or {}).get("code") == "restore_deferred":
                    saved.last_error = None  # The deferred engine is back.
            if (saved.last_error or {}).get("code") in LEGACY_CODES:
                saved.last_error = None  # A 0.7.x capacity verdict says nothing about 0.8.0.
            self.saved = saved
            self._save()
            if engine != self.safety.engine_id:
                self.safety.replace_pool(self._backend(engine))
            self.active = engine
            self.restore_watch = engine != catalog.DEFAULT_ENGINE
            self.notify()

    async def _restore_problem(self, engine):
        name = _name(engine)
        try:
            state = await self.store.verify(engine, deep=True)
        except Exception as error:
            LOGGER.warning("Could not verify the %s model (%s)", engine, type(error).__name__)
            state = None
        if state is None or state.state != "ready":
            return False, f"The {name} model files are missing or damaged."
        try:
            result = self._assess(engine, self._host_facts(fresh=True))
        except Exception as error:
            LOGGER.warning("Could not assess %s (%s)", engine, type(error).__name__)
            return True, f"Could not check whether {name} can run here."
        reasons = [r for r in result.reasons if r.code not in DOWNLOAD_REASONS]
        if reasons:
            return True, f"{name} cannot run here yet: {reasons[0].message}"
        return None

    async def start_safety(self) -> None:
        async with self.lock:
            if self.critical:
                return  # The switch task starts safety itself.
            await self.safety.start()
            if self.restore_watch:
                self.restore_watch = False
                self.watch = asyncio.create_task(self._watch_restore(self.active))

    async def _watch_restore(self, engine):
        ok, why = await self._outcome(patient=True)
        # Disabling resolves the outcome too; only a failed first load rolls back.
        if ok or not self.safety.desired_enabled:
            return
        if self.safety.phase == "configuration":
            return  # A host problem that would block Whistle as well, not the engine.
        async with self.lock:
            if self.busy or self.active != engine:
                return
            self.status = SwitchStatus(
                state="rolling_back", target=engine, previous=catalog.DEFAULT_ENGINE,
                started_at=self.clock(), updated_at=self.clock(),
            )
            try:
                await self._rollback(catalog.DEFAULT_ENGINE, engine, _failure_code(why), why)
            except asyncio.CancelledError:
                await self._abandon(engine, catalog.DEFAULT_ENGINE)
                raise

    async def stop(self) -> None:
        for task in (self.watch, self.task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    LOGGER.exception("Engine switch ended with an error")
        self.watch = None

    # Switching

    def _refuse_if_locked(self):
        """Refuse at once instead of queueing behind a running switch: a queued request would
        start a second change nobody confirmed once the first one finished."""
        if self.busy:
            raise SwitchRefused(409, "busy", "A speech engine change is already in progress")
        if self.lock.locked():
            raise SwitchRefused(409, "busy", "Cassini Live is busy; try again in a moment")

    async def request_switch(self, engine: str) -> SwitchStatus:
        self._refuse_if_locked()
        async with self.lock:
            if self.busy:
                raise SwitchRefused(409, "busy", "A speech engine change is already in progress")
            if engine not in catalog.ENGINES:
                raise SwitchRefused(400, "unknown_engine", "Unknown speech engine")
            if not self.service.enabled:
                raise SwitchRefused(409, "disabled", "Cassini Live is not enabled")
            if engine == self.active:
                if self.saved.engine != engine:
                    # Confirming the running fallback drops a deferred choice for good.
                    self._save(engine=engine, last_error=None)
                    return self.status
                raise SwitchRefused(400, "active", f"{_name(engine)} is already active")
            before, self.last_refusal = self.status, None
            self.cancel_reason = None
            now = self.clock()
            self.status = SwitchStatus(
                "validating", engine, self.active, now, now, "Checking the requirements"
            )
            self.notify()
        try:
            if catalog.get(engine).asset and self.store.state(engine).state != "ready":
                await self.refresh_network([engine])
            async with self.lock:
                self._validate(engine)
                self.cancel_reason = None
                self._save(last_error=None)
                self.task = asyncio.create_task(self._run(engine, self.status.previous))
        except BaseException:
            self.status = before
            self.notify()
            raise
        return self.status

    def _validate(self, engine):
        result = self._assess(engine, self._host_facts(fresh=True))
        if not result.eligible:
            raise SwitchRefused(
                400, "not_eligible", f"{_name(engine)} cannot run here.", result.reasons
            )
        if not self.service.enabled:
            raise SwitchRefused(409, "disabled", "Cassini Live is not enabled")

    async def _stage(self, awaitable):
        """Run one awaited step so that cancel() can interrupt it without killing the task."""
        self.step = asyncio.ensure_future(awaitable)
        try:
            return await self.step
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling() or not self.cancel_reason:
                raise
            raise _Interrupted() from None
        finally:
            self.step = None

    async def _run(self, target, previous):
        try:
            if not await self._prepare(target):
                return
            async with self.lock:
                if self.cancel_reason:  # Cancelled while waiting for the lock.
                    self._finish("cancelled", {"code": "cancelled", "message": self.cancel_reason})
                    return
                await self._replace(target, previous)
        except asyncio.CancelledError:
            await self._abandon(target, previous)
            raise
        except Exception:
            LOGGER.exception("Engine switch failed")
            if self.busy:
                message = f"The change to {_name(target)} failed unexpectedly"
                self._finish("failed", {"code": "load_failed", "message": message})

    def _finish(self, state, error=None, rolled_back=False):
        message = {"succeeded": f"Now using {_name(self.active)}"}.get(state, "")
        self._set(state, message, error=error, rolled_back=rolled_back)

    async def _prepare(self, target):
        """Download and verify while the old engine keeps serving. False: switch ended."""
        if catalog.get(target).asset is None:
            return True
        try:
            if self.store.state(target).state != "ready":
                self._set("downloading", f"Downloading {_name(target)}")
                progress = asyncio.create_task(self._report_progress(target))
                try:
                    await self._stage(self.store.download(target))
                finally:
                    progress.cancel()
            self._set("verifying", f"Verifying {_name(target)}")
            state = await self._stage(self.store.verify(target, deep=True))
        except ModelError as error:
            cancelled = error.code == "cancelled"
            message = (self.cancel_reason or str(error)) if cancelled else str(error)
            self._finish(
                "cancelled" if cancelled else "failed", {"code": error.code, "message": message}
            )
            return False
        except _Interrupted:
            self._finish("cancelled", {"code": "cancelled", "message": self.cancel_reason})
            return False
        if state.state != "ready":
            message = state.error or "The model files do not match their pinned checksums"
            self._finish("failed", {"code": "hash_mismatch", "message": message})
            return False
        return True

    async def _report_progress(self, target):
        last = None
        while True:
            await asyncio.sleep(1)
            progress = self.store.progress(target)
            current = progress and (progress.phase, progress.bytes_done)
            if current != last:
                last = current
                self.notify()

    async def _replace(self, target, previous):
        """Critical section: captions are down from here until the new engine is ready."""
        self._set("stopping", "Stopping captions")
        self._save(engine=previous, pending=target)
        code, why = "load_failed", ""
        try:
            await self._stage(self.safety.close(reason="Changing the speech engine"))
            self._set("loading", f"Loading {_name(target)}")
            self.safety.replace_pool(self._backend(target))
            ok, why = await self._stage(self._load())
            code = _failure_code(why)
        except _Interrupted:
            ok, code, why = False, "cancelled", self.cancel_reason
        except Exception as error:
            LOGGER.warning("Could not load %s (%s)", target, type(error).__name__)
            ok, why = False, f"{_name(target)} could not be loaded"
        if ok:
            self.active = target
            self._save(engine=target, pending=None)
            self._finish("succeeded")
            return
        await self._rollback(previous, target, code, why, cancelled=code == "cancelled")

    async def _load(self):
        await self.safety.start(fresh=True)
        return await self._outcome()

    def _timeout(self):
        if self.check_timeout is not None:
            return self.check_timeout
        return catalog.get(self.safety.engine_id).requirements.start_timeout_s + STOP_ROOMS_S

    async def _outcome(self, *, patient=False):
        """The load timeout bounds the attempt itself, not a cooldown before it."""
        timeout = self._timeout()
        while True:
            ok, why = await self.safety.wait_outcome(timeout)
            if ok or _failure_code(why) != "timeout":
                return ok, why
            safety = self.safety
            if safety.phase == "loading" and safety.attempt_started is not None:
                timeout = safety.attempt_started + self._timeout() - safety.clock()
                if timeout > 0:
                    continue  # The attempt began late in the wait; give it its full time.
            elif patient and safety.phase in WAITING_PHASES:
                timeout = self._timeout()
                continue  # Still in the recovery cooldown: no load has been attempted yet.
            return ok, why

    async def _rollback(self, previous, target, code, why, cancelled=False):
        self._set("rolling_back", f"Rolling back to {_name(previous)}")
        await self.safety.close(reason="Changing the speech engine")
        fallback = previous
        if catalog.get(previous).asset is not None:
            try:
                state = await self.store.verify(previous, deep=True)
            except Exception:
                state = None
            if state is None or state.state != "ready":
                fallback = catalog.DEFAULT_ENGINE
        try:
            self.safety.replace_pool(self._backend(fallback))
            self.active = fallback
            ok, _ = await self._load()
        except Exception as error:
            LOGGER.warning("Rollback to %s failed (%s)", fallback, type(error).__name__)
            ok = False
        self._save(engine=self.active, pending=None, last_error=self._error(code, why, target))
        self.status.previous = fallback
        self._finish(
            "cancelled" if cancelled else "failed", {"code": code, "message": why}, rolled_back=ok
        )

    async def _abandon(self, target, previous):
        """The service is being disabled: put the previous engine back without starting it."""
        try:
            if self.status.state == "downloading":
                self.store.cancel(target)
            if self.critical:
                await self.safety.close()
                if self.safety.engine_id != previous:
                    self.safety.replace_pool(self._backend(previous))
                self.active = previous
                self._save(engine=previous, pending=None)
        finally:
            message = "Cassini Live was disabled during the change"
            self._set("cancelled", "", error={"code": "disabled", "message": message})

    async def measure(self) -> dict:
        """Start an administrator's capacity measurement (0.8.0: never automatic)."""
        if self.busy or self.lock.locked():
            raise SwitchRefused(409, "busy", "Wait for the speech engine change to finish")
        try:
            await self.safety.measure(self.calls_starting)
        except ResourceRefused as refused:
            raise SwitchRefused(409, "unavailable", str(refused)) from None
        self.notify()
        return self.safety.assessment.result

    async def cancel(self, reason: str = "Cancelled by an administrator") -> SwitchStatus:
        if not self.busy or self.task is None:
            raise SwitchRefused(409, "idle", "No speech engine change is in progress")
        if self.status.state == "rolling_back":
            raise SwitchRefused(409, "busy", "The previous engine is being restored")
        if self.status.state == "validating":
            raise SwitchRefused(409, "busy", "The requirements are being checked; try again")
        self.cancel_reason = reason
        if self.status.state == "downloading":
            self.store.cancel(self.status.target)
        elif self.step is not None and not self.step.done():
            self.step.cancel()
        self.notify()
        return self.status

    # Models

    async def delete_model(self, engine: str) -> dict:
        if engine not in catalog.ENGINES:
            raise SwitchRefused(400, "unknown_engine", "Unknown speech engine")
        if self.critical or self.lock.locked():
            raise SwitchRefused(409, "busy", "Wait for the speech engine change to finish")
        async with self.lock:
            if engine == self.active or (self.busy and engine == self.status.target):
                raise SwitchRefused(409, "active", f"{_name(engine)} is in use")
            if engine == self.deferred:
                raise SwitchRefused(
                    409, "selected",
                    f"{_name(engine)} is selected and returns when Cassini Live restarts with "
                    f"suitable limits. Keep using {_name(self.active)} first to remove its files.",
                )
            if self.store.busy == engine:
                raise SwitchRefused(409, "busy", f"{_name(engine)} is downloading")
            if catalog.get(engine).asset is not None:
                await self.store.delete(engine)
            self.notify()
            return self._card(engine, self._host_facts())

    async def delete_unused_models(self) -> list[str]:
        # No lock: a running switch may hold it for minutes, and its models are kept.
        removed = []
        for engine, spec in catalog.ENGINES.items():
            keep = {self.active, self.status.target if self.busy else None, self.store.busy,
                    self.deferred}
            if spec.asset is None or engine in keep:
                continue
            if self.store.state(engine).state not in REMOVABLE:
                continue
            try:
                await self.store.delete(engine)
                removed.append(engine)
            except (ModelError, OSError) as error:
                LOGGER.warning("Could not remove the %s model (%s)", engine, error)
        if removed:
            self.notify()
        return removed

    def note_refusal(self, refused: SwitchRefused, engine: str) -> None:
        self.last_refusal = {
            "code": refused.code,
            "message": str(refused),
            "engine": engine,
            "reasons": [asdict(r) for r in refused.reasons],
            "at": round(self.clock()),
        }
        self.notify()

    async def refresh_network(self, engine_ids=None) -> None:
        if self.probe is None:
            return
        if engine_ids is None:
            engine_ids = [
                e for e, spec in catalog.ENGINES.items()
                if spec.asset is not None and self.store.state(e).state != "ready"
            ]
        if engine_ids:
            await self.probe.refresh(engine_ids)
            self._facts = None
            self.notify()

    # Views

    def languages(self) -> dict:
        engine = catalog.get(self.active)
        auto = "Automatic"
        if not engine.automatic_language:
            auto += f" ({LANGUAGE_NAMES[engine.languages[0]]})"
        return {
            "auto": {"name": auto, "metadata": {"separator": " ", "rtl": False}},
            **{code: LANGUAGES[code] for code in engine.languages},
        }

    def engine_status(self) -> dict:
        engine = catalog.get(self.active)
        return {
            "id": engine.id,
            "name": engine.name,
            "languages": list(engine.languages),
            "automaticLanguage": engine.automatic_language,
            "license": engine.license.name,
            "switching": self.busy,
            "switchState": self.status.state,
        }

    def _card(self, engine_id, facts):
        engine = catalog.get(engine_id)
        need, asset, license = engine.requirements, engine.asset, engine.license
        model = self.store.state(engine_id)
        progress = self.store.progress(engine_id)
        result = self._assess(engine_id, facts)
        memory = catalog.memory_floor_mib(engine_id, self._size(engine_id))
        return {
            "id": engine.id,
            "name": engine.name,
            "active": engine_id == self.active,
            # Selected but waiting for a restart with suitable limits; counts as in use.
            "deferred": engine_id == self.deferred,
            "summary": engine.summary,
            "accuracy": {"meetingWer": engine.meeting_wer, "note": engine.accuracy_note},
            "languages": list(engine.languages),
            "automaticLanguage": engine.automatic_language,
            "requirements": {
                "memoryLimitMiB": memory,
                "recommendedMemoryLimitMiB": need.recommended_memory_mib,
                "hostMemoryMiB": 2 * memory,
                "cpuQuota": need.min_cpu_quota,
                "downloadBytes": asset.archive_bytes if asset else 0,
                "diskBytes": catalog.download_disk_bytes(engine_id),
                "cpuFlags": list(need.cpu_flags),
                "provisional": need.provisional,
            },
            "license": {
                "id": license.id,
                "name": license.name,
                "url": license.url,
                "version": license.version,
                "notice": license.notice,
                "summary": list(license.summary),
            },
            "model": {
                "state": model.state,
                "bytesDone": progress.bytes_done if progress else model.partial_bytes,
                "bytesTotal": (
                    progress.bytes_total if progress else asset.archive_bytes if asset else 0
                ),
                "error": model.error,
            },
            "eligible": result.eligible,
            "advisory": result.advisory,
            "reasons": [asdict(r) for r in result.reasons],
        }

    def panel(self) -> dict:
        facts = self._host_facts()
        switch = self.status.to_json()
        progress = self.store.progress(self.status.target) if self.busy else None
        switch["progress"] = progress and {
            "phase": progress.phase,
            "bytesDone": progress.bytes_done,
            "bytesTotal": progress.bytes_total,
        }
        return {
            "active": self.active,
            "selected": self.desired,
            "activeCalls": len(getattr(self.service, "clients", ())),
            "measurements": {
                "measuredOn": catalog.MEASURED_ON, "estimates": list(catalog.ESTIMATED)
            },
            "switch": switch,
            "lastError": self.saved.last_error,
            "lastRefusal": self.last_refusal,
            "host": {
                "cpuQuota": facts.cpu_quota,
                "affinityCpus": facts.affinity_cpus,
                "memoryLimitBytes": facts.memory_limit_bytes,
                "hostMemoryBytes": facts.host_memory_bytes,
                "freeDiskBytes": facts.free_disk_bytes,
                "arch": facts.arch,
                "cpuFlags": sorted(facts.cpu_flags & CPU_FLAGS),
            },
            "engines": [self._card(e, facts) for e in catalog.ENGINES],
        }
