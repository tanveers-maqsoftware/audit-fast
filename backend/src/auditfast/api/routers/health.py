"""Health and configuration status."""

from __future__ import annotations

from fastapi import APIRouter

from auditfast import services
from auditfast.api.schemas import StatusResponse

router = APIRouter(tags=["health"])


@router.get("/api/health")
async def health() -> dict:
    """Liveness probe."""
    return {"status": "ok"}


@router.get("/api/status", response_model=StatusResponse)
async def status() -> dict:
    """Sign-in state and server configuration — the first call the UI makes."""
    return services.status()
