"""Bridge between the declarative admin settings form and the engine manager.

AppAPI stores the form's values in the ExApp's app config and sends no change event, so
the ExApp polls them. Declarative forms cannot disable options or update live, so the
ExApp re-registers the form when its status changes and rejects invalid choices by
writing the active engine back. A sync failure never affects captions.
"""

import asyncio
import logging
import time
from collections.abc import Callable

from . import admin_form, catalog
from .engine_switch import EngineManager, SwitchRefused

LOGGER = logging.getLogger(__name__)

POLL_S = 3.0  # appconfig_ex poll while enabled
BACKOFF_S = 30.0  # after a failed OCS call; reset on success
PUBLISH_MIN_S = 3.0  # at most one register_form per 3 s (trailing publish guaranteed)
PROGRESS_STEP = 5  # percent; admin_form rounds progress down to this step
OCS_TIMEOUT_S = 10.0
# Form fields of 0.7.0 (license tick box, cleanup checkbox), removed once from app config.
LEGACY_KEYS = ["nvidia_oml_2025_10_24_accepted", "remove_unused_models"]
LEGACY_ATTEMPTS = 3
NOT_RUNNING = "It was chosen while Cassini Live was not running. Choose it again."


class SettingsSync:
    def __init__(self, manager: EngineManager, nc_factory, status: Callable[[], dict], *,
                 build=admin_form.build_form, parse=admin_form.parse_values,
                 clock=time.monotonic, poll_s=POLL_S, publish_min_s=PUBLISH_MIN_S,
                 page_url: str = admin_form.DEFAULT_PAGE_URL):
        self.manager, self.nc_factory, self.status = manager, nc_factory, status
        self.build, self.parse, self.clock = build, parse, clock
        self.poll_s, self.publish_min_s = poll_s, publish_min_s
        self.page_url = page_url
        self.delay = poll_s
        self.notes: list[str] = []
        self.nc = None
        self.first = True
        self.seen: str | None = None
        self.stored: str | None = None
        self.legacy_attempts = 0
        self.legacy_cleared = False
        self.sent: dict | None = None
        self.published_at = -float("inf")
        self.publish_failed = False
        self.logged: set[str] = set()
        self.dirty = asyncio.Event()
        self.poller: asyncio.Task | None = None
        self.publisher: asyncio.Task | None = None

    # Nextcloud access

    async def _ocs(self, call):
        if self.nc is None:
            self.nc = self.nc_factory()
        try:
            async with asyncio.timeout(OCS_TIMEOUT_S):
                result = await call(self.nc)
        except Exception:
            self.nc = None  # Recreate the client after any error.
            raise
        self.logged.clear()
        return result

    def _log(self, what, error):
        kind = f"{what}:{type(error).__name__}"
        if kind not in self.logged:
            self.logged.add(kind)
            LOGGER.warning("Admin settings %s failed (%s)", what, type(error).__name__)

    async def _write(self, engine):
        await self._ocs(lambda nc: nc.appconfig_ex.set_value(admin_form.ENGINE_FIELD, engine))
        self.stored = self.seen = engine
        self.notify()

    def _note(self, text, on=True):
        if on and text not in self.notes:
            self.notes.append(text)
            self.notify()
        elif not on and text in self.notes:
            self.notes.remove(text)
            self.notify()

    # Lifecycle

    async def start(self) -> None:
        if self.poller and not self.poller.done():
            return
        self.manager.on_change = self.notify
        self.first, self.sent, self.delay = True, None, self.poll_s
        self.poller = asyncio.create_task(self._poll_loop())
        self.publisher = asyncio.create_task(self._publish_loop())

    async def stop(self, final: str | None = None) -> None:
        if self.manager.on_change == self.notify:
            self.manager.on_change = None
        for task in (self.poller, self.publisher):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self.poller = self.publisher = None
        if final:
            try:
                async with asyncio.timeout(5):
                    form = self._build((final,))
                    await self._ocs(lambda nc: nc.ui.settings.register_form(form))
            except Exception as error:
                self._log("final publish", error)

    def _build(self, notes):
        return self.build(self.manager.panel(), self.status(), notes, page_url=self.page_url)

    def notify(self) -> None:
        self.dirty.set()

    async def _poll_loop(self):
        await self.publish(force=True)
        try:
            await self.manager.refresh_network()
        except Exception as error:
            self._log("network probe", error)
        while True:
            await self.poll_once()
            await asyncio.sleep(self.delay)

    async def _publish_loop(self):
        while True:
            await self.dirty.wait()
            self.dirty.clear()
            wait = self.published_at + self.publish_min_s - self.clock()
            if wait > 0:
                await asyncio.sleep(wait)
            await self.publish()

    # Publishing

    async def publish(self, *, force: bool = False) -> None:
        try:
            form = self._build(tuple(self.notes))
            data = form.to_dict()
            if data == self.sent and not force:
                return
            await self._ocs(lambda nc: nc.ui.settings.register_form(form))
        except Exception as error:
            self.publish_failed = True
            self._log("publish", error)
            return
        self.sent, self.published_at, self.publish_failed = data, self.clock(), False

    # Polling

    async def poll_once(self) -> None:
        fields = [admin_form.ENGINE_FIELD]
        try:
            records = await self._ocs(lambda nc: nc.appconfig_ex.get_values(fields))
            await self._clear_legacy()
            await self._decide(self.parse(records))
            self.delay = self.poll_s
        except Exception as error:
            self._log("poll", error)
            self.delay = BACKOFF_S
        # Captions and a capacity measurement change outside the manager; an identical form is
        # skipped without an OCS call, so this republishes only when the text changed.
        self.notify()

    async def _clear_legacy(self):
        """Best effort: a failure only leaves two unused app config values behind."""
        if self.legacy_cleared or self.legacy_attempts >= LEGACY_ATTEMPTS:
            return
        self.legacy_attempts += 1
        try:
            await self._ocs(lambda nc: nc.appconfig_ex.delete(LEGACY_KEYS))
            self.legacy_cleared = True
        except Exception as error:
            self._log("legacy cleanup", error)

    async def _decide(self, selection):
        manager = self.manager
        self.stored = selection.engine
        if self.first:
            # Never act on a choice saved while the ExApp was down or left by a rollback.
            self.first, self.seen = False, selection.engine
            if selection.engine is not None and selection.engine != manager.desired:
                last_error = getattr(getattr(manager, "saved", None), "last_error", None) or {}
                explained = last_error.get("engine") == selection.engine  # e.g. restore_failed
                if selection.engine in catalog.ENGINES and not explained:
                    manager.note_refusal(SwitchRefused(409, "not_running", NOT_RUNNING),
                                         selection.engine)
                await self._write(manager.desired)
        elif selection.engine is not None and selection.engine != self.seen:
            self.seen = selection.engine
            await self._choose(selection.engine)
        status = manager.status
        cancelling = getattr(manager, "cancel_reason", None)
        if manager.busy and status.target and self.stored != status.target and not cancelling:
            # A change started on the settings page: show it as if chosen here, with the
            # previous engine as "select to cancel". Not while it is being cancelled: the
            # admin's choice of the previous engine stays until the change has ended.
            await self._write(status.target)
        if not manager.busy:
            self._note(self._busy_note(), False)
            if self.stored is not None and self.stored != manager.desired:
                await self._write(manager.desired)

    def _busy_note(self, target=None):
        target = target or self.manager.status.target
        name = catalog.get(target).name if target in catalog.ENGINES else "another engine"
        return f"A change to {name} is in progress. Wait for it to finish."

    async def _choose(self, engine):
        manager = self.manager
        if engine not in catalog.ENGINES:
            manager.note_refusal(
                SwitchRefused(400, "unknown_engine", "Unknown speech engine"), engine
            )
            await self._write(manager.desired)
        elif manager.busy:
            status = manager.status
            if engine == status.previous and status.state in admin_form.CANCELLABLE:
                try:
                    await manager.cancel("Cancelled in the administration settings")
                except SwitchRefused as refused:
                    manager.note_refusal(refused, engine)
            else:
                self._note(self._busy_note(status.target))
                await self._write(status.target)
        elif engine != manager.desired:
            # Choosing the running engine while another one waits for suitable limits drops
            # that choice (request_switch confirms the active engine).
            await self._request(engine)

    async def _request(self, engine):
        manager = self.manager
        try:
            await manager.request_switch(engine)
        except SwitchRefused as refused:
            manager.note_refusal(refused, engine)
            await self._write(manager.desired)
