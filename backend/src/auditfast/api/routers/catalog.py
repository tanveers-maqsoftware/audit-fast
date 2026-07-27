"""The check catalog and user-added checklist items."""

from __future__ import annotations

from fastapi import APIRouter

from auditfast import services
from auditfast.api.deps import unwrap
from auditfast.api.schemas import CustomCheckRequest

router = APIRouter(prefix="/api", tags=["catalog"])


@router.get("/checks")
async def baseline_checks() -> dict:
    """The deterministic baseline catalog."""
    return services.list_checks()


@router.get("/custom-checks")
async def list_custom_checks(engagement_id: str | None = None) -> dict:
    """User-added checklist items — all of them, or those in scope for one engagement."""
    return services.list_custom_checks(engagement_id)


@router.post("/custom-checks", status_code=201)
async def add_custom_check(body: CustomCheckRequest, engagement_id: str | None = None) -> dict:
    """Add a checklist item. Omit engagement_id to make it available to every engagement."""
    return unwrap(services.add_custom_check(body.model_dump(), engagement_id))


@router.delete("/custom-checks/{check_id}")
async def delete_custom_check(check_id: str) -> dict:
    return unwrap(services.delete_custom_check(check_id), status=404)
