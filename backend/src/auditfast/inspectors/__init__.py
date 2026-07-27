"""Inspector suite — read-only evidence collection, one inspector per artifact type."""

from auditfast.inspectors.base import (
    ArtifactEvidence,
    EvidenceBundle,
    Inspector,
    WorkspaceEvidence,
)
from auditfast.inspectors.definitions import (
    DefinitionInspector,
    NotebookInspector,
    PipelineInspector,
)
from auditfast.inspectors.registry import collect_evidence, discover_inventory
from auditfast.inspectors.workspace import WorkspaceInspector

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
