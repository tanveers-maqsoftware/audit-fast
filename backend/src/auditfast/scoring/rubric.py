"""Scoring rubric.

The 0-3 anchors and the rollup arithmetic come from the baseline rubric
(Local/Audit Excel 1/Audit/01-scoring-rubric.md):

    Category score = sum(item scores) / (3 x applicable items) x 100%
    Area score     = mean of its category scores
    Overall        = sum(area weight x area score), renormalized over audited areas

The coverage bands are Core-specific: a rule-based engine measures "what fraction of
objects pass" and has to map that onto the same 0-3 scale a human auditor would use.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum

# Area weights, verbatim from 02-audit-checklist.md.
AREA_WEIGHTS: dict[int, float] = {
    1: 8.0,
    2: 10.0,
    3: 8.0,
    4: 7.0,
    5: 10.0,
    6: 12.0,
    7: 12.0,
    8: 5.0,
    9: 5.0,
    10: 5.0,
    11: 7.0,
    12: 7.0,
    13: 4.0,
}

AREA_NAMES: dict[int, str] = {
    1: "Architecture & Design",
    2: "Data Integration & Ingestion",
    3: "Data Processing & Transformation",
    4: "Data Modeling & Storage",
    5: "Data Quality Framework",
    6: "Security & Access Control",
    7: "Compliance & Regulatory",
    8: "Data Governance",
    9: "Reliability & Resilience",
    10: "Monitoring & Observability",
    11: "DevOps & Deployment",
    12: "Cost Management & Capacity",
    13: "Documentation & Knowledge Management",
}

# Pillars roll up from each check's own `pillar` tag, not from its area.
#
# docs/11-baseline-checklist-catalog.md sketches an area -> pillar map, but areas and
# pillars are not aligned one-to-one: check 3.1.3 (secrets hardcoded in notebooks) sits
# in Area 3 yet is unambiguously a Security concern. Rolling by area produced reports
# showing "Security 100%" directly above a CRITICAL security finding. Rolling by the
# per-item tag the catalog already carries keeps the scorecard honest.

# coverage -> score. A near-miss is "implemented with minor issues"; below 40% of objects
# passing is not a partial implementation, it is an absent practice.
_COVERAGE_BANDS: tuple[tuple[float, int], ...] = (
    (0.95, 3),
    (0.80, 2),
    (0.40, 1),
    (0.0, 0),
)


class RiskBand(StrEnum):
    CRITICAL = "Critical"
    HIGH = "High"
    MEDIUM = "Medium"
    GOOD = "Good"
    EXCELLENT = "Excellent"

    @property
    def emoji(self) -> str:
        return {
            RiskBand.CRITICAL: "🔴",
            RiskBand.HIGH: "🟠",
            RiskBand.MEDIUM: "🟡",
            RiskBand.GOOD: "🟢",
            RiskBand.EXCELLENT: "🔵",
        }[self]


def risk_band(percentage: float) -> RiskBand:
    """Risk bands from 01-scoring-rubric.md section 5."""
    if percentage <= 40:
        return RiskBand.CRITICAL
    if percentage <= 60:
        return RiskBand.HIGH
    if percentage <= 75:
        return RiskBand.MEDIUM
    if percentage <= 90:
        return RiskBand.GOOD
    return RiskBand.EXCELLENT


def score_from_coverage(coverage: float) -> int:
    """Map a 0.0-1.0 pass fraction onto the 0-3 rubric."""
    clamped = max(0.0, min(1.0, coverage))
    for threshold, score in _COVERAGE_BANDS:
        if clamped >= threshold:
            return score
    return 0


def score_from_binary(passed: bool) -> int:
    return 3 if passed else 0


@dataclass
class ScoredItem:
    item_id: str
    area: int
    category: str
    pillar: str
    score: int | None  # None = N/A or evidence unavailable
    status: str  # scored | not_applicable | evidence_unavailable


@dataclass
class Rollup:
    categories: dict[str, float] = field(default_factory=dict)
    areas: dict[int, float] = field(default_factory=dict)
    pillars: dict[str, float] = field(default_factory=dict)
    overall: float = 0.0
    areas_audited: list[int] = field(default_factory=list)
    items_scored: int = 0
    items_not_applicable: int = 0
    items_unavailable: int = 0

    @property
    def band(self) -> RiskBand:
        return risk_band(self.overall)


def roll_up(items: list[ScoredItem]) -> Rollup:
    """Aggregate scored items into category, area, pillar, and overall percentages."""
    rollup = Rollup()

    scored = [i for i in items if i.status == "scored" and i.score is not None]
    rollup.items_scored = len(scored)
    rollup.items_not_applicable = sum(1 for i in items if i.status == "not_applicable")
    rollup.items_unavailable = sum(1 for i in items if i.status == "evidence_unavailable")

    if not scored:
        return rollup

    # Category = sum(scores) / (3 * count).
    by_category: dict[tuple[int, str], list[int]] = defaultdict(list)
    for item in scored:
        by_category[(item.area, item.category)].append(item.score or 0)

    area_categories: dict[int, list[float]] = defaultdict(list)
    for (area, category), scores in by_category.items():
        pct = sum(scores) / (3 * len(scores)) * 100
        rollup.categories[f"{area} — {category}"] = round(pct, 1)
        area_categories[area].append(pct)

    # Area = mean of its categories.
    for area, pcts in area_categories.items():
        rollup.areas[area] = round(sum(pcts) / len(pcts), 1)
    rollup.areas_audited = sorted(rollup.areas)

    # Overall = area weights renormalized across the areas actually audited, so a
    # partial-coverage Core scan is not diluted by the ten areas it never looked at.
    total_weight = sum(AREA_WEIGHTS[a] for a in rollup.areas)
    if total_weight:
        rollup.overall = round(
            sum(AREA_WEIGHTS[a] * pct for a, pct in rollup.areas.items()) / total_weight, 1
        )

    # Pillars use the same item-level formula as categories, over each check's own tag.
    by_pillar: dict[str, list[int]] = defaultdict(list)
    for item in scored:
        by_pillar[item.pillar].append(item.score or 0)
    for pillar, scores in by_pillar.items():
        rollup.pillars[pillar] = round(sum(scores) / (3 * len(scores)) * 100, 1)

    return rollup
