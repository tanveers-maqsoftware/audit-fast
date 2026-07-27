"""The engagement lifecycle: start → discover → confirm → run → report.

Each route is a thin adapter over ``services``; the confirmation gate and read-only
guarantee are enforced in the service/guardrail layers, not here.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auditfast import services
from auditfast.api.deps import unwrap
from auditfast.api.schemas import (
    ConfirmScopeRequest,
    Engagement,
    ManualScoreRequest,
    StartEngagementRequest,
)

router = APIRouter(prefix="/api/engagements", tags=["engagements"])


@router.get("")
async def list_engagements() -> dict:
    return services.list_engagements()


@router.post("", response_model=None, status_code=201)
async def start_engagement(body: StartEngagementRequest) -> dict:
    return unwrap(services.start_engagement(body.workspace_url, body.project_name, body.notes))


@router.get("/{engagement_id}", response_model=Engagement)
async def get_engagement(engagement_id: str) -> dict:
    engagement = services.get_engagement(engagement_id)
    if engagement is None:
        raise HTTPException(status_code=404, detail={"error": "Unknown engagement."})
    return engagement


@router.post("/{engagement_id}/discover")
async def discover(engagement_id: str) -> dict:
    return unwrap(await services.discover_workspace(engagement_id))


@router.post("/{engagement_id}/confirm")
async def confirm(engagement_id: str, body: ConfirmScopeRequest) -> dict:
    return unwrap(
        services.confirm_scope(
            engagement_id,
            confirm=body.confirm,
            exclude_artifact_ids=body.exclude_artifact_ids,
            include_artifact_ids=body.include_artifact_ids,
            exclude_item_ids=body.exclude_item_ids,
        )
    )


@router.post("/{engagement_id}/run")
async def run(engagement_id: str) -> dict:
    return unwrap(await services.run_audit(engagement_id))


@router.get("/{engagement_id}/report")
async def report(engagement_id: str) -> dict:
    return unwrap(services.get_report(engagement_id, save_to_file=True))


@router.post("/{engagement_id}/manual-score")
async def manual_score(engagement_id: str, body: ManualScoreRequest) -> dict:
    return unwrap(services.score_manual_check(engagement_id, body.item_id, body.score, body.note))


@router.get("/{engagement_id}/audit-log")
async def audit_log(engagement_id: str, limit: int = 100) -> dict:
    return services.get_audit_log(engagement_id, limit)
