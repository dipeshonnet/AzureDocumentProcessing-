from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.health import router as health_router
from app.api.auth import router as auth_router
from app.api.intake import router as intake_router
from app.api.rubrics_ops import router as rubric_ops_router
from app.api.v1.router import router as api_v1_router
from app.config import configure_logging, get_settings
from app.security import development_auth_middleware
from app.services.intake_jobs import ensure_intake_worker_started, stop_intake_worker


logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    from app.services.free_hosting import validate_free_hosting
    validate_free_hosting(settings)
    if settings.app_env == "azure-mvp" and (settings.dev_auth_enabled or settings.expose_legacy_api):
        raise RuntimeError("Azure MVP requires DEV_AUTH_ENABLED=false and EXPOSE_LEGACY_API=false.")
    configure_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Reviewer decision-support API for admissions document workflows.",
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def attach_development_actor(request, call_next):
        return await development_auth_middleware(request, call_next, settings)

    app.include_router(health_router)
    app.include_router(auth_router)
    from app.api.workflow import router as workflow_router
    app.include_router(workflow_router)
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm.exc import StaleDataError
    from fastapi.responses import JSONResponse

    @app.exception_handler(StaleDataError)
    @app.exception_handler(IntegrityError)
    async def workflow_conflict(_request, _exc):
        return JSONResponse(status_code=409, content={"detail": "Another operation changed this record or identifier. Reload before saving."})
    app.include_router(intake_router)
    app.include_router(rubric_ops_router)
    @app.get("/api/runtime", include_in_schema=False)
    def runtime_config():
        mock = settings.parser_mode.strip().lower() == 'mock' or any(
            backend.strip().lower() == 'mock' for backend in (
                settings.document_classification_backend, settings.structured_extraction_backend,
                settings.document_summarization_backend, settings.rubric_scoring_backend))
        return {"demo_mode": mock}
    if settings.expose_legacy_api:
        app.include_router(api_v1_router, prefix="/api/v1")

    if settings.frontend_dist_path:
        app.mount("/", StaticFiles(directory=Path(settings.frontend_dist_path), html=True), name="frontend")

    @app.on_event("startup")
    async def startup_intake_worker() -> None:
        if settings.database_url and settings.intake_worker_enabled:
            await ensure_intake_worker_started(app, settings)

    @app.on_event("shutdown")
    async def shutdown_intake_worker() -> None:
        await stop_intake_worker(app)

    logger.info("Admissions AI backend initialized for environment '%s'", settings.app_env)
    return app


app = create_app()
