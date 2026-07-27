"""Workspace-level evidence: properties, roles, Git, deployment pipelines, capacity."""

from __future__ import annotations

from typing import Any

from auditfast.fabric.client import FabricClient
from auditfast.guardrail.core import EvidenceUnavailable
from auditfast.inspectors.base import WorkspaceEvidence


class WorkspaceInspector:
    artifact_type = "workspace"

    async def discover(self, client: FabricClient, workspace_id: str) -> WorkspaceEvidence:
        """Inventory only — enough to propose a scope, cheap enough to always run."""
        evidence = WorkspaceEvidence(workspace_id=workspace_id)
        workspace = await client.get_workspace(workspace_id)
        evidence.name = workspace.get("displayName", "")
        evidence.description = workspace.get("description", "") or ""
        evidence.capacity_id = workspace.get("capacityId")
        evidence.items = await client.list_items(workspace_id)
        return evidence

    async def inspect(
        self, client: FabricClient, workspace_id: str, *, evidence: WorkspaceEvidence | None = None
    ) -> WorkspaceEvidence:
        """Add the evidence the workspace-level rules need."""
        ev = evidence or await self.discover(client, workspace_id)

        ev.role_assignments = await self._safe_list(
            client.get_role_assignments(workspace_id), ev, "role assignments"
        )
        ev.git_connection = await self._safe_value(
            client.get_git_connection(workspace_id), ev, "git connection"
        )
        ev.deployment_pipelines = await self._deployment_pipelines_for(client, workspace_id, ev)
        ev.capacity = await self._capacity_for(client, ev)
        return ev

    # -- helpers --------------------------------------------------------------

    async def _safe_list(self, coro: Any, ev: WorkspaceEvidence, label: str) -> list[dict]:
        try:
            return await coro
        except EvidenceUnavailable as exc:
            ev.collection_errors.append(f"{label}: {exc}")
            return []

    async def _safe_value(self, coro: Any, ev: WorkspaceEvidence, label: str) -> dict | None:
        try:
            return await coro
        except EvidenceUnavailable as exc:
            ev.collection_errors.append(f"{label}: {exc}")
            return None

    async def _deployment_pipelines_for(
        self, client: FabricClient, workspace_id: str, ev: WorkspaceEvidence
    ) -> list[dict]:
        """Deployment pipelines whose stages bind this workspace."""
        bound: list[dict] = []
        try:
            pipelines = await client.list_deployment_pipelines()
        except EvidenceUnavailable as exc:
            ev.collection_errors.append(f"deployment pipelines: {exc}")
            return bound

        for pipeline in pipelines:
            pipeline_id = pipeline.get("id")
            if not pipeline_id:
                continue
            stages = await client.get_deployment_pipeline_stages(pipeline_id)
            if any(
                str(stage.get("workspaceId", "")).lower() == workspace_id.lower()
                for stage in stages
            ):
                bound.append({**pipeline, "stages": stages, "stageCount": len(stages)})
        return bound

    async def _capacity_for(self, client: FabricClient, ev: WorkspaceEvidence) -> dict | None:
        if not ev.capacity_id:
            return None
        capacities = await client.list_capacities()
        wanted = str(ev.capacity_id).lower()
        return next((c for c in capacities if str(c.get("id", "")).lower() == wanted), None)
