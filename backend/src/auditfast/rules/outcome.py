"""The result shape every rule returns."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class RuleStatus(StrEnum):
    SCORED = "scored"
    NOT_APPLICABLE = "not_applicable"
    EVIDENCE_UNAVAILABLE = "evidence_unavailable"


@dataclass
class RuleOutcome:
    status: RuleStatus
    detail: str
    coverage: float | None = None
    passed: bool | None = None
    objects_inspected: int = 0
    failing_objects: list[str] = field(default_factory=list)

    @classmethod
    def coverage_result(
        cls,
        passing: list[str],
        failing: list[str],
        detail: str,
    ) -> RuleOutcome:
        total = len(passing) + len(failing)
        if total == 0:
            return cls(RuleStatus.NOT_APPLICABLE, detail="No applicable objects found.")
        return cls(
            status=RuleStatus.SCORED,
            detail=detail,
            coverage=len(passing) / total,
            objects_inspected=total,
            # Cap the list so a 400-object workspace does not flood the transcript.
            failing_objects=failing[:25],
        )

    @classmethod
    def binary_result(
        cls, passed: bool, detail: str, failing: list[str] | None = None
    ) -> RuleOutcome:
        return cls(
            status=RuleStatus.SCORED,
            detail=detail,
            passed=passed,
            coverage=1.0 if passed else 0.0,
            objects_inspected=1,
            failing_objects=(failing or [])[:25],
        )

    @classmethod
    def not_applicable(cls, reason: str) -> RuleOutcome:
        return cls(RuleStatus.NOT_APPLICABLE, detail=reason)

    @classmethod
    def unavailable(cls, reason: str) -> RuleOutcome:
        return cls(RuleStatus.EVIDENCE_UNAVAILABLE, detail=reason)
