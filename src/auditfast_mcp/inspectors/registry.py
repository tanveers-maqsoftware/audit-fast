"""Inspector registry — the one place that knows which inspector handles what.

Adding a Fabric artifact type means adding an inspector and one registry entry; the
orchestration below does not change (HLD 9, "Extensibility").
"""

from __future__ import annotations

from auditfast_mcp.config import Settings, get_settings
from auditfast_mcp.fabric.client import FabricClient
from auditfast_mcp.inspectors.base import ArtifactEvidence, EvidenceBundle, WorkspaceEvidence
from auditfast_mcp.inspectors.definitions import (
    DefinitionInspector,
    NotebookInspector,
    PipelineInspector,
)
from auditfast_mcp.inspectors.workspace import WorkspaceInspector

DEFINITION_INSPECTORS: tuple[DefinitionInspector, ...] = (
    PipelineInspector(),
    NotebookInspector(),
)


async def discover_inventory(client: FabricClient, workspace_id: str) -> WorkspaceEvidence:
    """Discovery pass — inventory and workspace properties only, no definitions."""
    return await WorkspaceInspector().discover(client, workspace_id)


async def collect_evidence(
    client: FabricClient,
    workspace_id: str,
    *,
    selected_artifact_ids: set[str] | None = None,
    settings: Settings | None = None,
    workspace_evidence: WorkspaceEvidence | None = None,
) -> EvidenceBundle:
    """Full evidence pass over the confirmed scope."""
    cfg = settings or get_settings()

    workspace = await WorkspaceInspector().inspect(
        client, workspace_id, evidence=workspace_evidence
    )

    artifacts: list[ArtifactEvidence] = []
    for inspector in DEFINITION_INSPECTORS:
        items = [i for i in workspace.items if i.get("type") == inspector.item_type]
        if selected_artifact_ids is not None:
            items = [i for i in items if i.get("id") in selected_artifact_ids]
        if len(items) > cfg.max_items_inspected:
            workspace.collection_errors.append(
                f"{inspector.artifact_type}: inspected the first {cfg.max_items_inspected} "
                f"of {len(items)} items (AUDITFAST_MAX_ITEMS_INSPECTED)"
            )
            items = items[: cfg.max_items_inspected]
        artifacts.extend(
            await inspector.inspect(
                client,
                workspace_id,
                items=items,
                fetch_definitions=cfg.enable_definition_reads,
            )
        )

    return EvidenceBundle(
        workspace=workspace,
        artifacts=artifacts,
        definitions_enabled=cfg.enable_definition_reads,
        definition_failures=sum(1 for a in artifacts if a.definition_error),
    )
