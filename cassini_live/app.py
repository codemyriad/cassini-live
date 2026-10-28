"""Authenticated Nextcloud ExApp and native Talk caption provider."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from typing import Literal

from fastapi import Body, FastAPI, HTTPException, Request
from nc_py_api import AsyncNextcloudApp
from nc_py_api.ex_app import run_app, set_handlers
from pydantic import BaseModel, Field

from . import __version__, catalog, diagnostics_page, panel_page, settings_page, transcriber
from .auth import SafeAppAPIAuthMiddleware
from .capacity import CapacityAssessment
from .config import Config, state_dir
from .eligibility import NetworkProbe
from .engine_switch import EngineManager, SwitchRefused
from .membership import RoomMembership
from .model_store import ModelStore
from .pool import WorkerPool
from .recovery import RecoveryJournal
from .safety import ResourceRefused, SafetyController
from .service import CaptionService
from .settings import SettingsStore
from .settings_sync import SettingsSync
from .talk.types import SpreedClientException, VoskException

LOGGER = logging.getLogger(__name__)
Language = Literal["auto", "en", "de", "fr", "es", "it", "nl", "pl"]
DISABLED_NOTE = "Cassini Live is disabled. Enable it to change the speech engine."
STOPPED_NOTE = "Cassini Live is restarting or stopped. Reload this page in a minute."


class TranscribeRequest(BaseModel):
    roomToken: str = Field(min_length=1, max_length=128)
    ncSessionId: str = Field(min_length=1, max_length=512)
    enable: bool = True
    langId: Language = "auto"
    translationTargetLangId: str | None = None


class LanguageRequest(BaseModel):
    roomToken: str = Field(min_length=1, max_length=128)
    langId: Language


def create_app(
    config=None,
    pool=None,
    service=None,
    nc_factory=AsyncNextcloudApp,
    safety_factory=SafetyController,
    discovery_delay=15,
):
    config = config or Config.from_env()
    # Production wiring builds its own pool; tests inject one and get no settings sync.
    production = pool is None
    pool = pool or WorkerPool(config.workers, config.weights)
    service = service or CaptionService(config)
    assessment = CapacityAssessment(pool)
    safety = safety_factory(pool, assessment, service)
    storage = state_dir()
    if safety_factory is SafetyController:
        safety.journal = RecoveryJournal(storage / "cassini-live-recovery.json")
    service.admission = safety
    manager = EngineManager(
        safety,
        service,
        config,
        ModelStore(storage / "models" if production else None,
                   calls_active=lambda: bool(safety.tracks)),
        SettingsStore(storage / "cassini-live-settings.json" if production else None),
        probe=NetworkProbe() if production else None,
    )
    sync = None
    call_starts = 0
    # A measurement must not start while a call's captions are being set up.
    manager.calls_starting = lambda: call_starts
    discovery_task = None
    enable_generation = 0

    async def restore_connection(nc, force=False):
        async with asyncio.timeout(10):
            if not force and not await nc.enabled_state:
                safety.state, safety.phase = "disabled", "disabled"
                safety.reason = "Caption service stopped"
                return
            if sync:
                # Before discovery, so the form can show why Talk is not ready.
                await sync.start()
            await service.discover(nc)
        service.enabled = True
        await manager.start_safety()

    async def retry_connection(force=False):
        delay = discovery_delay
        while True:
            safety.state, safety.phase = "recovering", "waiting_talk"
            safety.reason = "Waiting for Nextcloud and Talk; connection will retry automatically"
            if not config.secret:
                safety.state, safety.phase = "blocked", "configuration"
                safety.reason = "Configure the Talk HPB internal secret in deploy options"
                safety.admin_action = True
            await asyncio.sleep(delay)
            try:
                await restore_connection(nc_factory(), force)
                return
            except Exception:
                service.enabled = False
                service.settings = None
                delay = min(60, delay * 2)

    async def stop_discovery():
        nonlocal discovery_task
        if discovery_task:
            discovery_task.cancel()
            with suppress(asyncio.CancelledError):
                await discovery_task
            discovery_task = None

    async def remove_legacy_page(nc):
        # 0.6.x registered a menu page; the engine choice now lives in admin settings.
        try:
            await nc.ui.resources.delete_script("top_menu", "live", "ui/live")
            await nc.ui.top_menu.unregister("live")
        except Exception as error:
            LOGGER.debug("Could not remove the legacy page (%s)", type(error).__name__)

    async def enabled_handler(enabled, nc):
        nonlocal discovery_task, enable_generation
        enable_generation += 1
        generation = enable_generation
        await stop_discovery()
        if not enabled:
            await manager.stop()
            if sync:
                await sync.stop(final=DISABLED_NOTE)
            await safety.close()
            await service.close()
            return ""
        if sync:
            await sync.start()
        try:
            await service.discover(nc)
            await remove_legacy_page(nc)
            if generation != enable_generation:
                return ""
            service.enabled = True
            if not safety.desired_enabled:
                await manager.start_safety()
            return ""
        except Exception:
            if generation != enable_generation:
                return ""
            service.enabled = False
            service.settings = None
            LOGGER.warning("Talk is not ready; connection will retry automatically")
            discovery_task = asyncio.create_task(retry_connection(force=True))
            return ""

    @asynccontextmanager
    async def lifespan(app):
        nonlocal discovery_task
        if not os.environ.get("APP_SECRET"):
            raise RuntimeError("APP_SECRET is required; register Cassini Live through AppAPI")
        if os.environ.get("APP_ID") != "live_transcription":
            raise RuntimeError("Talk requires APP_ID=live_transcription")
        transcriber.POOL = pool
        try:
            if production:
                await manager.restore()
            try:
                await restore_connection(nc_factory())
            except Exception:
                LOGGER.warning("Talk is not ready on startup; retrying automatically")
                discovery_task = asyncio.create_task(retry_connection())
            yield
        finally:
            await stop_discovery()
            if sync:
                await sync.stop(final=STOPPED_NOTE)
            await manager.stop()
            await safety.close()
            await service.close()
            await safety.pool.close()
            transcriber.POOL = None

    app = FastAPI(
        title="Cassini Live", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.add_middleware(SafeAppAPIAuthMiddleware)
    set_handlers(app, enabled_handler, map_app_static=False)
    app.state.pool = pool
    app.state.service = service
    app.state.assessment = assessment
    app.state.safety = safety
    app.state.manager = manager

    def require_enabled():
        if not service.enabled:
            raise HTTPException(503, "Cassini Live is not enabled or Talk is not ready")

    @app.get("/enabled")
    async def enabled():
        return {"enabled": service.enabled}

    @app.get("/capabilities")
    async def capabilities():
        return {
            "live_transcription": {
                "version": __version__,
                "features": ["live_transcription"],
                "live_transcription": {"supported_languages": manager.languages()},
            }
        }

    @app.get("/api/v1/languages")
    async def languages():
        return manager.languages()

    def require_language(language):
        if language not in manager.languages():
            raise HTTPException(400, "The active speech engine does not support this language")

    @app.post("/api/v1/call/transcribe")
    async def transcribe_call(req: TranscribeRequest, request: Request):
        nonlocal call_starts
        require_enabled()
        if req.translationTargetLangId:
            raise HTTPException(400, "Cassini Live provides captions in the spoken language")
        require_language(req.langId)
        try:
            call_starts += 1
            try:
                if req.enable:
                    safety.require_ready()
                await service.request(req)
            finally:
                call_starts -= 1
        except ResourceRefused as error:
            raise HTTPException(
                503,
                str(error),
                headers={"Retry-After": str(safety.status()["retryAfterSeconds"] or 30)},
            ) from error
        except (SpreedClientException, TimeoutError) as error:
            raise HTTPException(
                503, "Could not connect captions to Talk; please try again"
            ) from error
        # AppAPI names the requesting user; the diagnostic video reads this room's
        # participant names as them. Guests have no user id and are skipped.
        client = getattr(service, "clients", {}).get(req.roomToken)
        if req.enable and client is not None and hasattr(client, "note_user"):
            client.note_user(request.scope.get("username") or None)
        return {"message": "Captions enabled" if req.enable else "Captions disabled"}

    @app.post("/api/v1/call/set-language")
    async def set_language(req: LanguageRequest):
        require_enabled()
        require_language(req.langId)
        try:
            await service.set_language(req)
        except KeyError:
            raise HTTPException(404, "No active captions in this call") from None
        except VoskException:
            raise HTTPException(
                503, "Could not change the caption language; please try again"
            ) from None
        return {"message": "Caption language updated"}

    @app.post("/api/v1/call/leave")
    async def leave(roomToken: str = Body(embed=True, min_length=1, max_length=128)):
        await service.leave(roomToken)
        return {"message": "Captions stopped"}

    def status_body():
        resource_status = safety.status()
        if discovery_task and not discovery_task.done():
            resource_status["automaticRecovery"] = True
        engine = catalog.get(manager.active)
        return {
            "name": "Cassini Live",
            "version": __version__,
            "enabled": service.enabled,
            "model": engine.name,
            "languages": list(engine.languages),
            "automaticLanguage": engine.automatic_language,
            "signallingConfigured": service.settings is not None,
            "activeRooms": len(service.clients),
            "capacityAssessment": assessment.result,
            "resourceSafety": resource_status,
            "diagnosticVideo": service.diagnostics.status(),
            **safety.pool.status(),
            # After the pool fields: the pool reports only its engine id under this key.
            "engine": manager.engine_status(),
        }

    membership = RoomMembership(nc_factory)
    app.include_router(settings_page.create_router(manager, status_body))
    app.include_router(panel_page.create_router())
    app.include_router(
        diagnostics_page.create_router(
            service.diagnostic_feed, membership, service.diagnostics.status
        )
    )
    if production:
        sync = SettingsSync(manager, nc_factory, status_body, page_url=settings_page.page_url())

    @app.get("/status")
    async def status():
        return status_body()

    @app.get("/history")
    async def history(request: Request, token: str = "", since: int = 0):
        # Late-joiner catch-up (D-830): the current call's timeline in `token`
        # (finals from every speaker, plus participant joins) newer than `since`,
        # for a participant. The panel polls this with its last seq. USER-level at
        # the proxy; membership re-checked here because any logged-in user can
        # reach the route.
        if not token:
            raise HTTPException(400, "A room token is required")
        client = getattr(service, "clients", {}).get(token)
        if client is None:
            # No active caption client — nothing has been transcribed to catch up on.
            return {"entries": [], "head": 0}
        if not await membership.check(request.scope.get("username") or "", token):
            raise HTTPException(403, "Not a participant of this call")
        return {"entries": client.history_snapshot(since), "head": client.history_head()}

    @app.post("/capacity/check")
    async def check_capacity(request: Request):
        # Container CLI only (capacity --installed --measure); not a browser proxy route.
        # Administrators measure from the settings page, behind its CSRF guard.
        if request.scope.get("username") != "":
            raise HTTPException(403, "Run the capacity check from the app container")
        try:
            result = await manager.measure()
        except SwitchRefused as refused:
            raise HTTPException(409, str(refused)) from None
        return {"state": result["state"]}

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    run_app(create_app(), access_log=False)
