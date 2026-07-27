"""Pydantic request/response models.

These define the HTTP contract that appears in the OpenAPI schema at ``/docs`` and
``/openapi.json`` — the same schema the frontend generates its typed client from.
Request models are strict (they validate input); response models describe the stable
shapes so the frontend has types to rely on.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# -- requests ----------------------------------------------------------------


class StartEngagementRequest(BaseModel):
    workspace_url: str = Field(..., description="Portal URL, workspace GUID, or display name.")
    project_name: str = Field(..., min_length=1)
    notes: str = ""


class ConfirmScopeRequest(BaseModel):
    confirm: bool = Field(False, description="Dry run unless true. True freezes the scope.")
    exclude_artifact_ids: list[str] | None = None
    include_artifact_ids: list[str] | None = None
    exclude_item_ids: list[str] | None = None


class PollRequest(BaseModel):
    poll_seconds: int = Field(15, ge=5, le=120)


class CustomCheckRequest(BaseModel):
    item_id: str = Field(..., description="Stable id, e.g. 'C.1.1' or an area-scoped id.")
    area: int = Field(..., ge=1, le=13)
    category: str
    title: str
    pillar: str = "Foundation"
    rationale: str = ""
    severity_hint: str = "medium"
    default_weight: str = "normal"


class ManualScoreRequest(BaseModel):
    item_id: str
    score: int | None = Field(None, ge=0, le=3, description="0–3, or null for Not Applicable.")
    note: str = ""


# -- responses ---------------------------------------------------------------


class StatusResponse(BaseModel):
    signed_in_as: str | None
    client_id_configured: bool
    tenant: str
    configuration_error: str
    definition_reads_enabled: bool
    data_dir: str
    checks_in_catalog: int
    limits: dict
    write_capability: str


class Engagement(BaseModel):
    id: str
    project_name: str
    workspace_url: str
    workspace_id: str | None
    workspace_name: str | None
    status: str
    created_at: str
    discovered_at: str | None
    scope_confirmed_at: str | None
    notes: str


class CustomCheck(BaseModel):
    id: str
    engagement_id: str | None
    item_id: str
    area: int
    category: str
    pillar: str
    title: str
    rationale: str
    severity_hint: str
    default_weight: str
    created_at: str
