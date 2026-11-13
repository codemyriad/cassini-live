"""Authenticated Nextcloud ExApp and native Talk caption provider."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from nc_py_api import AsyncNextcloudApp
from nc_py_api.ex_app import run_app, set_handlers
from pydantic import BaseModel, Field

from . import (
    __version__,
    appapi,
    catalog,
    diagnostics_page,
    home_page,
    mock_talk_proxy,
    panel_page,
    settings_page,
    transcriber,
)
from .auth import SafeAppAPIAuthMiddleware
from .capacity import CapacityAssessment
from .config import Config, state_dir
from .eligibility import NetworkProbe
from .engine_switch import EngineManager, SwitchRefused
from .membership import RoomMembership
from .model_store import ModelStore
from .nc_client import nc_client
from .pool import WorkerPool
from .recovery import RecoveryJournal
from .safety import ResourceRefused, SafetyController
from .service import CaptionService
from .settings import SettingsStore
from .settings_sync import SettingsSync
from .talk.types import SpreedClientException, VoskException
from .talk_setup import SECRET_MISSING, TalkSetup

LOGGER = logging.getLogger(__name__)
# The native caption API answers as nextcloud/live_transcription 2.1.3 does: the same
# routes, status codes, `{"message": ...}` on success and `{"error": ...}` on failure (the
# only key Talk reads), and its wording where it holds here. A malformed body is FastAPI's
# 422 on both sides. The one addition is the language "auto", also the default.
BAD_LANGUAGE = "Invalid or unsupported language ID provided."
NO_TRANSLATION = "Translation is not supported"
DISABLED_NOTE = "Cassini Live is disabled. Enable it to change the speech engine."
STOPPED_NOTE = "Cassini Live is restarting or stopped. Reload this page in a minute."


class TranscribeRequest(BaseModel):
    roomToken: str = Field(min_length=1, max_length=128)
    ncSessionId: str = Field(min_length=1, max_length=512)
    enable: bool = True
    langId: str = Field(default="auto", max_length=32)
    translationTargetLangId: str | None = Field(default=None, max_length=32)


class LanguageRequest(BaseModel):
    roomToken: str = Field(min_length=1, max_length=128)
    langId: str = Field(max_length=32)


class TargetLanguageRequest(BaseModel):
    roomToken: str = Field(min_length=1, max_length=128)
    ncSessionId: str = Field(min_length=1, max_length=512)
    langId: str | None = Field(default=None, max_length=32)


def failure(status: int, message: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"error": message}, status, headers=headers)


def create_app(
    config=None,
    pool=None,
    service=None,
    nc_factory=AsyncNextcloudApp,
    safety_factory=SafetyController,
    discovery_delay=15,
    app_id=None,
):
    config = config or Config.from_env()
    # The one read of the app id (see appapi): everything below gets it passed in.
    app_id = app_id or appapi.app_id_from_env()
    # Production wiring builds its own pool; tests inject one and get no settings sync.
    production = pool is None
    pool = pool or WorkerPool(config.workers, config.weights)
    storage = state_dir()
    if service is None:
        # The HPB secret and URL: deploy options first, then the settings page's file.
        talk_file = storage / "cassini-live-talk.json" if production else None
        talk = TalkSetup.from_config(config, talk_file)
        service = CaptionService(config, talk=talk)
    assessment = CapacityAssessment(pool)
    safety = safety_factory(pool, assessment, service)
    if safety_factory is SafetyController:
        safety.journal = RecoveryJournal(storage / "cassini-live-recovery.json")
    service.admission = safety
    manager = EngineManager(
        safety,
        service,
        config,
        ModelStore(
            storage / "models" if production else None, calls_active=lambda: bool(safety.tracks)
        ),
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
            if not service.talk.secret()[0]:
                safety.state, safety.phase = "blocked", "configuration"
                safety.reason = SECRET_MISSING
                safety.admin_action = True
            await asyncio.sleep(delay)
            try:
                async with nc_client(nc_factory) as nc:
                    await restore_connection(nc, force)
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

    async def reconnect_talk():
        """After the settings page changed the HPB secret or URL: discover again now rather
        than at the next retry, then check that the HPB accepts the secret. Rooms already
        captioned keep their connection; new rooms use the new values."""
        nonlocal discovery_task
        await stop_discovery()
        try:
            async with nc_client(nc_factory) as nc, asyncio.timeout(10):
                await service.discover(nc)
        except Exception as error:
            service.enabled = False
            service.settings = None
            LOGGER.warning(
                "Talk is not ready after the HPB setting changed (%s)", type(error).__name__
            )
            discovery_task = asyncio.create_task(retry_connection())
            return
        service.enabled = True
        await service.check_hpb()
        if not safety.desired_enabled:
            discovery_task = asyncio.create_task(manager.start_safety())

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
            await home_page.unregister(nc)
            await manager.stop()
            if sync:
                await sync.stop(final=DISABLED_NOTE)
            await safety.close()
            await service.close()
            return ""
        if sync:
            await sync.start()
        # Before discovery: the menu entry is how an administrator reaches the page
        # that says what is still missing, so it must not wait for Talk.
        await remove_legacy_page(nc)
        await home_page.register(nc)
        try:
            await service.discover(nc)
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
        if not appapi.is_talk_provider(app_id):
            LOGGER.info(
                "Registered as %s: Talk's caption button does not reach this app; "
                "captions start from the transcript panel",
                app_id,
            )
        transcriber.POOL = pool
        try:
            if production:
                await manager.restore()
            try:
                async with nc_client(nc_factory) as nc:
                    await restore_connection(nc)
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
    app.add_middleware(SafeAppAPIAuthMiddleware, app_id=app_id)
    set_handlers(app, enabled_handler, map_app_static=False)
    app.state.pool = pool
    app.state.service = service
    app.state.assessment = assessment
    app.state.safety = safety
    app.state.manager = manager

    @app.exception_handler(SpreedClientException)
    async def transport_failure(_request, error: SpreedClientException):
        return failure(500, str(error))

    @app.get("/enabled")
    async def enabled():
        return {"enabled": service.enabled}

    @app.get("/capabilities")
    async def capabilities():
        # Talk's capability and feature names for a caption provider, not an app id.
        # No "live_translation": the answer of a provider that cannot translate.
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

    def room_client(token):
        client = getattr(service, "clients", {}).get(token)
        return None if client is None or client.defunct.is_set() else client

    @app.post("/api/v1/call/transcribe")
    async def transcribe_call(req: TranscribeRequest, request: Request):
        # Trusts its caller, as the upstream provider does: Talk authorizes its caption
        # button and then calls this server to server. Not a browser proxy route.
        nonlocal call_starts
        if not service.enabled:
            return failure(503, "Cassini Live is not enabled or Talk is not ready")
        if req.langId not in manager.languages():
            return failure(400, BAD_LANGUAGE)
        if req.enable and req.translationTargetLangId:
            client = room_client(req.roomToken)
            spoken = client.room_lang_id if client else req.langId
            if req.translationTargetLangId == spoken:
                return failure(
                    400,
                    f"Target language '{req.translationTargetLangId}' is the same as the "
                    f"original language '{spoken}'",
                )
            return failure(550, NO_TRANSLATION)
        try:
            call_starts += 1
            try:
                if req.enable:
                    safety.require_ready()
                await service.request(req)
            finally:
                call_starts -= 1
        except ResourceRefused as error:
            return failure(
                503,
                str(error),
                {"Retry-After": str(safety.status()["retryAfterSeconds"] or 30)},
            )
        except (SpreedClientException, TimeoutError):
            return failure(503, "Could not connect captions to Talk; please try again")
        except Exception as error:
            LOGGER.error("Caption request failed (%s)", type(error).__name__)
            return failure(500, "Failed to process transcription or translation request.")
        # AppAPI names the requesting user; the diagnostics page (for a member watching)
        # reads this room's participant names as them. Guests have no user id and are
        # skipped.
        client = getattr(service, "clients", {}).get(req.roomToken)
        if req.enable and client is not None and hasattr(client, "note_user"):
            client.note_user(request.scope.get("username") or None)
        return {"message": "Transcription or translation request processed successfully."}

    @app.post("/api/v1/call/set-language")
    async def set_language(req: LanguageRequest):
        if req.langId not in manager.languages():
            return failure(400, BAD_LANGUAGE)
        try:
            await service.set_language(req)
        except KeyError:
            # Talk reads 404 as "no captions running" and still stores the language.
            return failure(
                404,
                f"No SpreedClient for room token {req.roomToken}, cannot set language."
                " Start a call and add at least one participant first.",
            )
        except VoskException as error:
            return failure(error.retcode, "Failed to set language for the call")
        except Exception as error:
            LOGGER.error("Changing the caption language failed (%s)", type(error).__name__)
            return failure(500, "Failed to set language for the call")
        return {"message": "Language set successfully for the call"}

    @app.get("/api/v1/translation/languages")
    async def translation_languages(roomToken: str):
        # Talk reads 550 as "translation is not available": stop asking.
        return failure(550, "A fatal error occurred while fetching translation languages.")

    @app.post("/api/v1/translation/set-target-language")
    async def set_target_language(req: TargetLanguageRequest):
        client = room_client(req.roomToken)
        if client is None:
            return failure(
                404, f"No SpreedClient for room token {req.roomToken}, cannot set target language"
            )
        if req.langId is None:
            # Translation off: the session goes back to captions in the spoken language.
            await client.add_target(req.ncSessionId)
            return {"message": "Target translation language set successfully for the participant."}
        if req.langId == client.room_lang_id:
            return failure(
                400,
                f"Target language '{req.langId}' is the same as the original language "
                f"'{client.room_lang_id}'",
            )
        if not await client.is_target(req.ncSessionId):
            return failure(
                412,
                f"Transcript target with Nextcloud session ID '{req.ncSessionId}' not found."
                " Transcription must be enabled for the target before setting the"
                " translation language.",
            )
        return failure(550, NO_TRANSLATION)

    @app.post("/api/v1/call/leave")
    async def leave(roomToken: str = Body(embed=True)):
        try:
            await service.leave(roomToken)
        except Exception as error:
            LOGGER.error("Leaving a call failed (%s)", type(error).__name__)
            return failure(500, "Failed to process leave call request.")
        return {"message": "Leave call request processed."}

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
            **safety.pool.status(),
            # After the pool fields: the pool reports only its engine id under this key.
            "engine": manager.engine_status(),
        }

    membership = RoomMembership(nc_factory)
    app.include_router(
        settings_page.create_router(manager, status_body, service.talk, reconnect_talk)
    )
    app.include_router(panel_page.create_router(app_id))
    # Temporary: stands in for Talk until Talk can call this app id (see the module).
    app.state.native_api = mock_talk_proxy.NativeApi(app_id)
    app.include_router(mock_talk_proxy.create_router(nc_factory))
    app.include_router(home_page.create_router(app_id))
    app.include_router(diagnostics_page.create_router(service.diagnostic_feed, membership))
    if production:
        sync = SettingsSync(
            manager, nc_factory, status_body, page_url=settings_page.page_url(app_id)
        )

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
