"""Evidence shapes and the Inspector port (HLD 6.2).

An Inspector has two depths: ``discover`` is cheap and feeds the scope proposal;
``inspect`` collects the full evidence a rule needs. Both go through the guardrail —
an inspector never opens its own connection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from auditfast.fabric.client import FabricClient


@dataclass
class ArtifactEvidence:
    """One inspected item (a pipeline, a notebook, ...)."""

    artifact_id: str
    artifact_type: str
    item_type: str
    name: str
    description: str = ""
    definition: dict[str, Any] | None = None
    definition_error: str | None = None

    @property
    def has_definition(self) -> bool:
        return self.definition is not None


@dataclass
class WorkspaceEvidence:
    workspace_id: str
    name: str = ""
    description: str = ""
    capacity_id: str | None = None
    capacity: dict[str, Any] | None = None
    role_assignments: list[dict[str, Any]] = field(default_factory=list)
    git_connection: dict[str, Any] | None = None
    deployment_pipelines: list[dict[str, Any]] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)
    collection_errors: list[str] = field(default_factory=list)


@dataclass
class EvidenceBundle:
    """Everything the rule engine is allowed to reason over for one run."""

    workspace: WorkspaceEvidence
    artifacts: list[ArtifactEvidence] = field(default_factory=list)
    definitions_enabled: bool = False
    definition_failures: int = 0

    def of_type(self, artifact_type: str) -> list[ArtifactEvidence]:
        return [a for a in self.artifacts if a.artifact_type == artifact_type]

    @property
    def artifact_types_present(self) -> set[str]:
        types = {a.artifact_type for a in self.artifacts}
        types.update({"workspace", "git", "capacity"})
        return types


@runtime_checkable
class Inspector(Protocol):
    artifact_type: str

    async def discover(self, client: FabricClient, workspace_id: str) -> Any:
        """Cheap, read-only: names, counts, types. Feeds the scope proposal."""

    async def inspect(self, client: FabricClient, workspace_id: str, **kwargs: Any) -> Any:
        """Full read-only evidence collection."""
