"""Typed, read-only Fabric REST operations.

Each method describes a call and hands it to the guardrail. The client holds no
HTTP client and no token of its own — that asymmetry is the point.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from auditfast.guardrail.core import EvidenceUnavailable, Guardrail
from auditfast.guardrail.models import RestCall

FABRIC_API = "https://api.fabric.microsoft.com/v1"

# Item types AuditFAST can currently collect evidence for, mapped to the catalog's
# artifact_type enum (docs/11-baseline-checklist-catalog.md).
ITEM_TYPE_TO_ARTIFACT: dict[str, str] = {
    "DataPipeline": "pipeline",
    "Notebook": "notebook",
    "Lakehouse": "lakehouse",
    "Warehouse": "warehouse",
    "SemanticModel": "semantic_model",
    "Report": "report",
    "Eventhouse": "eventhouse",
    "KQLDatabase": "eventhouse",
    "Environment": "environment",
    "Dataflow": "dataflow",
    "SparkJobDefinition": "spark_job",
    "MirroredDatabase": "mirrored_database",
    "MLModel": "ml_model",
    "MLExperiment": "ml_experiment",
}


class FabricClient:
    def __init__(self, guardrail: Guardrail) -> None:
        self._guard = guardrail

    async def aclose(self) -> None:
        """Release the pooled connections held by the guardrail."""
        await self._guard.aclose()

    async def _get(self, path: str, purpose: str, params: dict[str, str] | None = None) -> Any:
        call = RestCall(
            method="GET", url=f"{FABRIC_API}{path}", purpose=purpose, params=params or {}
        )
        result = await self._guard.execute(call)
        return result.body

    async def _get_paged(self, path: str, purpose: str) -> list[dict]:
        """Fabric paginates with a continuationToken; follow it to exhaustion."""
        collected: list[dict] = []
        params: dict[str, str] = {}
        while True:
            body = await self._get(path, purpose, params=params)
            if not isinstance(body, dict):
                break
            collected.extend(body.get("value") or [])
            token = body.get("continuationToken")
            if not token:
                break
            params = {"continuationToken": token}
        return collected

    # -- workspace ------------------------------------------------------------

    async def list_workspaces(self) -> list[dict]:
        return await self._get_paged("/workspaces", "inventory: list workspaces visible to auditor")

    async def get_workspace(self, workspace_id: str) -> dict:
        body = await self._get(f"/workspaces/{workspace_id}", "inventory: workspace properties")
        return body if isinstance(body, dict) else {}

    async def resolve_workspace_by_name(self, name: str) -> dict | None:
        wanted = name.strip().casefold()
        for workspace in await self.list_workspaces():
            if str(workspace.get("displayName", "")).strip().casefold() == wanted:
                return workspace
        return None

    async def list_items(self, workspace_id: str) -> list[dict]:
        return await self._get_paged(
            f"/workspaces/{workspace_id}/items", "inventory: all items in workspace"
        )

    async def get_role_assignments(self, workspace_id: str) -> list[dict]:
        return await self._get_paged(
            f"/workspaces/{workspace_id}/roleAssignments",
            "evidence: workspace role assignments (Area 6.1)",
        )

    async def get_git_connection(self, workspace_id: str) -> dict | None:
        try:
            body = await self._get(
                f"/workspaces/{workspace_id}/git/connection",
                "evidence: Git integration status (Area 11.1)",
            )
        except EvidenceUnavailable:
            return None
        return body if isinstance(body, dict) else None

    async def list_capacities(self) -> list[dict]:
        try:
            return await self._get_paged("/capacities", "evidence: capacity SKUs (Area 12.1)")
        except EvidenceUnavailable:
            return []

    async def list_deployment_pipelines(self) -> list[dict]:
        try:
            return await self._get_paged(
                "/deploymentPipelines", "evidence: deployment pipelines (Area 11.2)"
            )
        except EvidenceUnavailable:
            return []

    async def get_deployment_pipeline_stages(self, pipeline_id: str) -> list[dict]:
        try:
            return await self._get_paged(
                f"/deploymentPipelines/{pipeline_id}/stages",
                "evidence: deployment pipeline stage bindings (Area 11.2)",
            )
        except EvidenceUnavailable:
            return []

    # -- item definitions -----------------------------------------------------

    async def get_item_definition(self, workspace_id: str, item_id: str, item_type: str) -> dict:
        """Read an item's definition. POST is the only verb Fabric offers for this read."""
        segment = {"DataPipeline": "dataPipelines", "Notebook": "notebooks"}.get(item_type, "items")
        call = RestCall(
            method="POST",
            url=f"{FABRIC_API}/workspaces/{workspace_id}/{segment}/{item_id}/getDefinition",
            purpose=f"evidence: {item_type} definition for rule evaluation",
        )
        result = await self._guard.execute(call)
        return result.body if isinstance(result.body, dict) else {}

    @staticmethod
    def decode_definition_parts(definition: dict) -> dict[str, Any]:
        """Decode the base64 parts of a getDefinition response into usable objects.

        Returns a mapping of part path -> parsed JSON (or raw text when it is not JSON).
        """
        parts: dict[str, Any] = {}
        for part in (definition.get("definition") or {}).get("parts") or []:
            path = part.get("path") or ""
            payload = part.get("payload")
            if not payload:
                continue
            try:
                raw = base64.b64decode(payload)
            except (ValueError, TypeError):
                continue
            text = raw.decode("utf-8", errors="replace")
            if path.endswith((".json", ".ipynb")) or text.lstrip().startswith(("{", "[")):
                try:
                    parts[path] = json.loads(text)
                    continue
                except ValueError:
                    pass
            parts[path] = text
        return parts
