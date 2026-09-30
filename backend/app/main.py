"""Application factory."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import __version__
from app.api import admin, auth, cases, evidence, system
from app.core.authentication import ProvisionedPasswordAuthenticator
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import (
    BodySizeLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from app.db.migrations import expected_head
from app.db.session import build_engine, build_session_factory
from app.services.evidence_storage import LocalEvidenceStorage

logger = logging.getLogger("veritas.app")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    engine = build_engine(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "VERITAS API starting",
            extra={
                "event": "startup",
                "environment": settings.environment,
                "access_mode": settings.access_mode,
            },
        )
        yield
        engine.dispose()

    docs = settings.api_docs_enabled
    app = FastAPI(
        title="VERITAS API",
        version=__version__,
        debug=False,  # never render framework debug pages, regardless of VERITAS_DEBUG
        lifespan=lifespan,
        redirect_slashes=False,
        docs_url="/api/docs" if docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if docs else None,
    )
    app.state.settings = settings
    app.state.authenticator = ProvisionedPasswordAuthenticator()
    app.state.engine = engine
    app.state.session_factory = build_session_factory(engine)
    app.state.expected_revision = expected_head()
    app.state.started_monotonic = time.monotonic()
    app.state.evidence_storage = LocalEvidenceStorage(settings.evidence_storage_root)

    register_error_handlers(app)
    app.include_router(system.router)
    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(cases.router)
    app.include_router(evidence.router)

    # Middleware: last added runs first (outermost).
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-Request-ID", "X-CSRF-Token"],
        expose_headers=["X-Request-ID"],
        allow_credentials=True,
        max_age=600,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    app.add_middleware(RequestContextMiddleware)
    # Outermost, so every response — including generated 500s and host rejections — is covered.
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.environment == "production")
    return app


def get_app() -> FastAPI:
    """Uvicorn factory entry point: ``uvicorn app.main:get_app --factory``."""
    return create_app()
