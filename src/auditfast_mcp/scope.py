"""Scope proposal — decide which discovered artifacts are worth auditing, and say why.

Every include and exclude carries a cited rationale (HLD G5, "explainable tailoring").
The auditor confirms or edits the proposal before any audit runs; nothing here is
applied automatically (HLD G6, human-in-the-loop gate 1).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from auditfast_mcp.catalog.loader import load_catalog
from auditfast_mcp.catalog.models import CheckRecord
from auditfast_mcp.config import Settings
from auditfast_mcp.fabric.client import ITEM_TYPE_TO_ARTIFACT
from auditfast_mcp.inspectors.base import WorkspaceEvidence

# Item types Core can currently collect evidence for.
AUDITABLE_ITEM_TYPES: frozenset[str] = frozenset({"DataPipeline", "Notebook"})

# Deliberate boundaries, each with the reason the auditor will see.
EXCLUSION_REASONS: dict[str, str] = {
    "SemanticModel": "Deep semantic-model certification is owned by CertyFAST, not AuditFAST.",
    "Report": "Deep report certification is owned by CertyFAST, not AuditFAST.",
    "Lakehouse": "Delta and table-level checks need the SQL analytics endpoint (Phase 2).",
    "Warehouse": "Warehouse checks need the SQL analytics endpoint (Phase 2).",
    "Eventhouse": "Eventhouse checks need a KQL connector (Phase 2).",
    "KQLDatabase": "KQL database checks need a KQL connector (Phase 2).",
    "Environment": "Environment/Spark-pool settings are read through Phase 2 connectors.",
    "Dataflow": "Dataflow Gen2 definitions are not covered by the MVP rule set.",
}


@dataclass
class ProposedArtifact:
    artifact_id: str
    artifact_type: str
    item_type: str
    name: str
    included: bool
    rationale: str


@dataclass
class ScopeProposal:
    workspace_id: str
    workspace_name: str
    project_name: str
    artifacts: list[ProposedArtifact] = field(default_factory=list)
    checks_in_scope: list[CheckRecord] = field(default_factory=list)
    checks_out_of_scope: list[tuple[CheckRecord, str]] = field(default_factory=list)
    inventory_counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def included_artifacts(self) -> list[ProposedArtifact]:
        return [a for a in self.artifacts if a.included]

    def to_dict(self) -> dict:
        return {
            "workspace_id": self.workspace_id,
            "workspace_name": self.workspace_name,
            "project_name": self.project_name,
            "inventory_counts": self.inventory_counts,
            "artifacts_relevant": [
                {
                    "id": a.artifact_id,
                    "name": a.name,
                    "type": a.item_type,
                    "why": a.rationale,
                }
                for a in self.artifacts
                if a.included
            ],
            "artifacts_excluded": [
                {
                    "id": a.artifact_id,
                    "name": a.name,
                    "type": a.item_type,
                    "why_not": a.rationale,
                }
                for a in self.artifacts
                if not a.included
            ],
            "checks_in_scope": [
                {
                    "item_id": c.item_id,
                    "area": c.area,
                    "title": c.title,
                    "pillar": c.pillar.value,
                    "needs_definition_read": c.requires_definition,
                }
                for c in self.checks_in_scope
            ],
            "checks_out_of_scope": [
                {"item_id": c.item_id, "title": c.title, "why_not": reason}
                for c, reason in self.checks_out_of_scope
            ],
            "warnings": self.warnings,
        }


def propose_scope(
    workspace: WorkspaceEvidence, project_name: str, settings: Settings
) -> ScopeProposal:
    """Turn a discovery inventory into a reviewable scope proposal."""
    proposal = ScopeProposal(
        workspace_id=workspace.workspace_id,
        workspace_name=workspace.name,
        project_name=project_name,
    )

    counts: dict[str, int] = {}
    for item in workspace.items:
        item_type = item.get("type", "Unknown")
        counts[item_type] = counts.get(item_type, 0) + 1

        included = item_type in AUDITABLE_ITEM_TYPES
        if included:
            rationale = (
                f"{item_type} definitions are readable over REST, so the MVP rule set can "
                "score it deterministically."
            )
        else:
            rationale = EXCLUSION_REASONS.get(
                item_type,
                f"No MVP rule covers {item_type} items yet; it stays in the inventory as context.",
            )

        proposal.artifacts.append(
            ProposedArtifact(
                artifact_id=item.get("id", ""),
                artifact_type=ITEM_TYPE_TO_ARTIFACT.get(item_type, item_type.lower()),
                item_type=item_type,
                name=item.get("displayName", ""),
                included=included,
                rationale=rationale,
            )
        )

    proposal.inventory_counts = dict(sorted(counts.items()))

    # A check is in scope when the artifacts it needs are actually present.
    present_types = {a.artifact_type for a in proposal.included_artifacts}
    present_types.update({"workspace", "git", "capacity"})

    for check in load_catalog():
        if not check.artifact_types or set(check.artifact_types) & present_types:
            proposal.checks_in_scope.append(check)
        else:
            needed = ", ".join(check.artifact_types)
            proposal.checks_out_of_scope.append(
                (check, f"No {needed} artifact is in scope for this workspace.")
            )

    _add_warnings(proposal, workspace, settings)
    return proposal


def _add_warnings(
    proposal: ScopeProposal, workspace: WorkspaceEvidence, settings: Settings
) -> None:
    definition_checks = [c for c in proposal.checks_in_scope if c.requires_definition]
    if definition_checks and not settings.enable_definition_reads:
        proposal.warnings.append(
            f"{len(definition_checks)} of {len(proposal.checks_in_scope)} in-scope checks read "
            "item definitions, which Fabric only exposes through an API requiring the "
            "Item.ReadWrite.All delegated scope. Definition reads are currently OFF, so those "
            "checks will report evidence_unavailable. Set AUDITFAST_ENABLE_DEFINITION_READS=true "
            "and sign in again to include them — the guardrail still blocks every write."
        )

    if len(proposal.included_artifacts) > settings.max_items_inspected:
        proposal.warnings.append(
            f"{len(proposal.included_artifacts)} artifacts are in scope but only the first "
            f"{settings.max_items_inspected} will be inspected "
            "(AUDITFAST_MAX_ITEMS_INSPECTED)."
        )

    if not workspace.items:
        proposal.warnings.append(
            "The workspace reports no items. Confirm the URL points at the right workspace "
            "and that the signed-in account can see its contents."
        )

    audited_areas = sorted({c.area for c in proposal.checks_in_scope})
    proposal.warnings.append(
        f"Core covers areas {audited_areas} of the 13-area baseline. The overall score is "
        "renormalized across audited areas only and is not comparable to a full deep-dive audit."
    )
