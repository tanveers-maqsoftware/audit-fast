"""Inspectors for items whose evidence lives in their definition JSON.

Pipelines and notebooks differ only in the item type they claim and the artifact type
they report, so they share one implementation.
"""

from __future__ import annotations

from auditfast_mcp.fabric.client import FabricClient
from auditfast_mcp.guardrail.core import EvidenceUnavailable, GuardrailRejection
from auditfast_mcp.inspectors.base import ArtifactEvidence


class DefinitionInspector:
    """Fetches and decodes item definitions for one Fabric item type."""

    item_type: str = ""
    artifact_type: str = ""

    async def discover(self, client: FabricClient, workspace_id: str) -> list[dict]:
        items = await client.list_items(workspace_id)
        return [i for i in items if i.get("type") == self.item_type]

    async def inspect(
        self,
        client: FabricClient,
        workspace_id: str,
        *,
        items: list[dict],
        fetch_definitions: bool = True,
    ) -> list[ArtifactEvidence]:
        collected: list[ArtifactEvidence] = []
        for item in items:
            evidence = ArtifactEvidence(
                artifact_id=item.get("id", ""),
                artifact_type=self.artifact_type,
                item_type=self.item_type,
                name=item.get("displayName", ""),
                description=item.get("description", "") or "",
            )
            if fetch_definitions and evidence.artifact_id:
                await self._attach_definition(client, workspace_id, evidence)
            collected.append(evidence)
        return collected

    async def _attach_definition(
        self, client: FabricClient, workspace_id: str, evidence: ArtifactEvidence
    ) -> None:
        try:
            raw = await client.get_item_definition(
                workspace_id, evidence.artifact_id, self.item_type
            )
            evidence.definition = FabricClient.decode_definition_parts(raw)
        except GuardrailRejection as exc:
            # A rejection is a correctness signal, not a data problem — surface it loudly.
            evidence.definition_error = f"guardrail rejected the definition read: {exc}"
        except EvidenceUnavailable as exc:
            evidence.definition_error = str(exc)


class PipelineInspector(DefinitionInspector):
    item_type = "DataPipeline"
    artifact_type = "pipeline"


class NotebookInspector(DefinitionInspector):
    item_type = "Notebook"
    artifact_type = "notebook"
