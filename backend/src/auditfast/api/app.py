"""FastAPI application factory.

Assembles the routers, enables CORS for the frontend dev server, and ensures the
database schema exists on startup. Business logic lives in ``services``; this module
only wires HTTP to it.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from auditfast import __version__
from auditfast.api.routers import auth, catalog, engagements, health
from auditfast.config import get_settings
from auditfast.db import init_db


def _cors_origins() -> list[str]:
    raw = os.environ.get(
        "AUDITFAST_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",  # Vite dev server defaults
    )
    return [o.strip() for o in raw.split(",") if o.strip()]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Create tables if they do not exist. Production still runs Alembic migrations;
    # this makes a fresh dev/test database usable with zero setup.
    get_settings().ensure_dirs()
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="AuditFAST API",
        version=__version__,
        summary="Read-only Microsoft Fabric workspace auditing.",
        description=(
            "Drive an engagement end to end: sign in, discover a workspace, confirm the "
            "proposed scope, run the deterministic audit, and read the report. Every "
            "Fabric call is read-only and recorded in a hash-chained audit log."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(engagements.router)
    app.include_router(catalog.router)

    return app


app = create_app()
