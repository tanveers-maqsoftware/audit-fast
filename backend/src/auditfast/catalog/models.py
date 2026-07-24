"""Check-record schema — the runtime half of docs/11-baseline-checklist-catalog.md."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Pillar(StrEnum):
    RELIABILITY = "Reliability"
    SECURITY = "Security"
    COST_OPTIMIZATION = "CostOptimization"
    OPERATIONAL_EXCELLENCE = "OperationalExcellence"
    PERFORMANCE_EFFICIENCY = "PerformanceEfficiency"
    FOUNDATION = "Foundation"


class AutomationTier(StrEnum):
    MVP_AUTO = "mvp-auto"
    PHASE2 = "phase2"
    MANUAL = "manual"


class CoverageSemantics(StrEnum):
    COVERAGE = "coverage"
    BINARY = "binary"


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CheckRecord(BaseModel):
    """One auditable check, runnable as a pure function over collected evidence."""

    item_id: str
    area: int = Field(ge=1, le=13)
    category: str
    pillar: Pillar
    title: str
    artifact_types: list[str] = Field(default_factory=list)
    inspector: str
    protocol: str
    evidence: str
    rule: str
    rule_id: str
    coverage_semantics: CoverageSemantics
    automation_tier: AutomationTier = AutomationTier.MVP_AUTO
    common: bool = True
    requires_definition: bool = False
    applicability: str = "always"
    na_rule: str = ""
    default_weight: str = "normal"
    severity_hint: Severity = Severity.MEDIUM
    remediation: str = ""

    @property
    def area_category(self) -> str:
        return f"{self.area}.{self.category}"
