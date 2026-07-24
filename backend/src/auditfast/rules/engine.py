"""Rule dispatch and scoring.

Given a catalog record and an evidence bundle, produce a scored result. Pure and
synchronous — no I/O happens here, which is what makes the whole scoring path
unit-testable without touching Fabric.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from auditfast_mcp.catalog.models import CheckRecord, CoverageSemantics
from auditfast_mcp.config import Settings, get_settings
from auditfast_mcp.inspectors.base import EvidenceBundle
from auditfast_mcp.rules import notebook_rules, pipeline_rules, workspace_rules
from auditfast_mcp.rules.outcome import RuleOutcome, RuleStatus
from auditfast_mcp.scoring.rubric import score_from_binary, score_from_coverage

RuleFn = Callable[[EvidenceBundle, Settings], RuleOutcome]

RULES: dict[str, RuleFn] = {
    # Workspace / Areas 1, 6, 11, 12
    "workspace_naming_convention": workspace_rules.workspace_naming_convention,
    "workspace_least_privilege": workspace_rules.workspace_least_privilege,
    "workspace_group_based_access": workspace_rules.workspace_group_based_access,
    "workspace_spn_for_automation": workspace_rules.workspace_spn_for_automation,
    "workspace_no_guest_access": workspace_rules.workspace_no_guest_access,
    "workspace_git_connected": workspace_rules.workspace_git_connected,
    "workspace_deployment_pipeline": workspace_rules.workspace_deployment_pipeline,
    "workspace_capacity_assigned": workspace_rules.workspace_capacity_assigned,
    "workspace_no_orphaned_items": workspace_rules.workspace_no_orphaned_items,
    # Pipelines / Area 2
    "pipeline_naming_convention": pipeline_rules.pipeline_naming_convention,
    "pipeline_parameterized": pipeline_rules.pipeline_parameterized,
    "pipeline_annotations": pipeline_rules.pipeline_annotations,
    "pipeline_retry_configured": pipeline_rules.pipeline_retry_configured,
    "pipeline_retry_bounds": pipeline_rules.pipeline_retry_bounds,
    "pipeline_failure_paths": pipeline_rules.pipeline_failure_paths,
    "pipeline_failure_notification": pipeline_rules.pipeline_failure_notification,
    "pipeline_no_hardcoded_secrets": pipeline_rules.pipeline_no_hardcoded_secrets,
    # Notebooks / Area 3
    "notebook_naming_convention": notebook_rules.notebook_naming_convention,
    "notebook_parameterized": notebook_rules.notebook_parameterized,
    "notebook_no_hardcoded_secrets": notebook_rules.notebook_no_hardcoded_secrets,
    "notebook_timeout_configured": notebook_rules.notebook_timeout_configured,
}


@dataclass
class CheckResult:
    """One evaluated check, ready to persist and to render into the report."""

    item_id: str
    title: str
    area: int
    category: str
    pillar: str
    status: str
    score: int | None
    coverage: float | None
    detail: str
    severity: str | None
    remediation: str
    objects_inspected: int = 0
    failing_objects: list[str] = field(default_factory=list)

    @property
    def is_finding(self) -> bool:
        """Scores of 0 or 1 become findings and land in the risk register."""
        return self.status == "scored" and self.score is not None and self.score <= 1

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "area": self.area,
            "category": self.category,
            "pillar": self.pillar,
            "status": self.status,
            "score": self.score,
            "coverage": round(self.coverage, 3) if self.coverage is not None else None,
            "detail": self.detail,
            "severity": self.severity,
            "objects_inspected": self.objects_inspected,
            "failing_objects": self.failing_objects,
            "remediation": self.remediation if self.is_finding else "",
        }


def evaluate_check(
    check: CheckRecord, bundle: EvidenceBundle, settings: Settings | None = None
) -> CheckResult:
    cfg = settings or get_settings()
    rule = RULES.get(check.rule_id)

    if rule is None:
        outcome = RuleOutcome.unavailable(
            f"No rule implementation is registered for {check.rule_id!r}."
        )
    else:
        try:
            outcome = rule(bundle, cfg)
        except Exception as exc:  # a broken rule must not abort the whole run
            outcome = RuleOutcome.unavailable(
                f"Rule {check.rule_id} raised {type(exc).__name__}: {exc}"
            )

    score: int | None = None
    if outcome.status is RuleStatus.SCORED:
        if check.coverage_semantics is CoverageSemantics.BINARY:
            score = score_from_binary(bool(outcome.passed))
        else:
            score = score_from_coverage(outcome.coverage or 0.0)

    return CheckResult(
        item_id=check.item_id,
        title=check.title,
        area=check.area,
        category=check.category,
        pillar=check.pillar.value,
        status=outcome.status.value,
        score=score,
        coverage=outcome.coverage,
        detail=outcome.detail,
        severity=check.severity_hint.value if score is not None and score <= 1 else None,
        remediation=check.remediation,
        objects_inspected=outcome.objects_inspected,
        failing_objects=outcome.failing_objects,
    )


def run_rules(
    checks: list[CheckRecord], bundle: EvidenceBundle, settings: Settings | None = None
) -> list[CheckResult]:
    cfg = settings or get_settings()
    return [evaluate_check(check, bundle, cfg) for check in checks]
