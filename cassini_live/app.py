"""Authenticated Nextcloud ExApp and native Talk caption provider."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Literal

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from nc_py_api import AsyncNextcloudApp
from nc_py_api.ex_app import run_app, set_handlers
from pydantic import BaseModel, Field

from . import __version__, transcriber
from .auth import SafeAppAPIAuthMiddleware
from .capacity import CapacityAssessment
from .config import LANGUAGES, Config
from .pool import WorkerPool
from .recovery import RecoveryJournal
from .safety import ResourceRefused, SafetyController
from .service import CaptionService
from .talk.types import SpreedClientException, VoskException

LOGGER = logging.getLogger(__name__)
ASSETS = Path(__file__).parent / "static"
Language = Literal["auto", "en", "de", "fr", "es", "it", "nl", "pl"]
ALL_LANGUAGES = {
    "auto": {"name": "Automatic", "metadata": {"separator": " ", "rtl": False}},
    **LANGUAGES,
}


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
    pool = pool or WorkerPool(config.workers, config.weights)
    service = service or CaptionService(config)
    assessment = CapacityAssessment(pool)
    safety = safety_factory(pool, assessment, service)
    if safety_factory is SafetyController:
        storage = Path(os.environ.get("APP_PERSISTENT_STORAGE", "/home/cassini/.local/state"))
        safety.journal = RecoveryJournal(storage / "cassini-live-recovery.json")
        safety.auto_size = not bool(os.environ.get("CASSINI_LIVE_WORKERS"))
    service.admission = safety
    call_starts = 0
    discovery_task = None
    enable_generation = 0

    async def restore_connection(nc, force=False):
        async with asyncio.timeout(10):
            if not force and not await nc.enabled_state:
                safety.state, safety.phase = "disabled", "disabled"
                safety.reason = "Caption service stopped"
                return
            await service.discover(nc)
        service.enabled = True
        await safety.start()

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

    async def enabled_handler(enabled, nc):
        nonlocal discovery_task, enable_generation
        enable_generation += 1
        generation = enable_generation
        await stop_discovery()
        if not enabled:
            await safety.close()
            await service.close()
            return ""
        try:
            await service.discover(nc)
            await nc.ocs(
                "POST",
                "/ocs/v2.php/apps/app_api/api/v1/ui/top-menu",
                json={
                    "name": "live",
                    "displayName": "Cassini Live",
                    "icon": "img/app.svg",
                    "adminRequired": 0,
                },
            )
            await nc.ocs(
                "POST",
                "/ocs/v2.php/apps/app_api/api/v1/ui/script",
                json={
                    "type": "top_menu",
                    "name": "live",
                    "path": "ui/live",
                },
            )
            if generation != enable_generation:
                return ""
            service.enabled = True
            if not safety.desired_enabled:
                await safety.start()
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
            try:
                await restore_connection(nc_factory())
            except Exception:
                LOGGER.warning("Talk is not ready on startup; retrying automatically")
                discovery_task = asyncio.create_task(retry_connection())
            yield
        finally:
            await stop_discovery()
            await safety.close()
            await service.close()
            await pool.close()
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
                "live_transcription": {"supported_languages": ALL_LANGUAGES},
            }
        }

    @app.get("/api/v1/languages")
    async def languages():
        return ALL_LANGUAGES

    @app.post("/api/v1/call/transcribe")
    async def transcribe_call(req: TranscribeRequest):
        nonlocal call_starts
        require_enabled()
        if req.translationTargetLangId:
            raise HTTPException(400, "Cassini Live provides captions in the spoken language")
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
        return {"message": "Captions enabled" if req.enable else "Captions disabled"}

    @app.post("/api/v1/call/set-language")
    async def set_language(req: LanguageRequest):
        require_enabled()
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

    @app.get("/status")
    async def status():
        resource_status = safety.status()
        if discovery_task and not discovery_task.done():
            resource_status["automaticRecovery"] = True
        return {
            "name": "Cassini Live",
            "version": __version__,
            "enabled": service.enabled,
            "model": "Whistle",
            "languages": list(LANGUAGES),
            "automaticLanguage": True,
            "signallingConfigured": service.settings is not None,
            "activeRooms": len(service.clients),
            "capacityAssessment": assessment.result,
            "resourceSafety": resource_status,
            **pool.status(),
        }

    @app.post("/capacity/check")
    async def check_capacity(request: Request):
        # Container CLI only. Not declared as a browser proxy route, and even a
        # proxied administrator request cannot launch resource-intensive work.
        if request.scope.get("username") != "":
            raise HTTPException(403, "Run the capacity check from the app container")
        if service.clients or call_starts:
            raise HTTPException(409, "Capacity check requires no active caption rooms")
        if safety.lock.locked() or safety.state == "checking":
            return {"state": safety.state}
        try:
            await safety.recheck()
        except ResourceRefused as error:
            raise HTTPException(503, str(error)) from error
        return {"state": safety.state}

    @app.get("/ui/live.js")
    async def script():
        return FileResponse(ASSETS / "live.js", media_type="text/javascript")

    @app.get("/ui/live.css")
    async def stylesheet():
        return FileResponse(ASSETS / "live.css", media_type="text/css")

    @app.get("/img/app.svg")
    async def icon():
        return FileResponse(ASSETS / "app.svg", media_type="image/svg+xml")

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    run_app(create_app(), access_log=False)
