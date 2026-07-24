"""Inspector suite — read-only evidence collection, one inspector per artifact type."""

from auditfast_mcp.inspectors.base import (
    ArtifactEvidence,
    EvidenceBundle,
    Inspector,
    WorkspaceEvidence,
)
from auditfast_mcp.inspectors.definitions import (
    DefinitionInspector,
    NotebookInspector,
    PipelineInspector,
)
from auditfast_mcp.inspectors.registry import collect_evidence, discover_inventory
from auditfast_mcp.inspectors.workspace import WorkspaceInspector

__all__ = [
    "ArtifactEvidence",
    "DefinitionInspector",
    "EvidenceBundle",
    "Inspector",
    "NotebookInspector",
    "PipelineInspector",
    "WorkspaceEvidence",
    "WorkspaceInspector",
    "collect_evidence",
    "discover_inventory",
]
