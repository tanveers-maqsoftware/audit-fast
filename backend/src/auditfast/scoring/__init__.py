"""Deterministic scoring — coverage to 0-3, then category/area/pillar rollup."""

from auditfast.scoring.rubric import (
    AREA_WEIGHTS,
    RiskBand,
    Rollup,
    risk_band,
    roll_up,
    score_from_binary,
    score_from_coverage,
)

__all__ = [
    "AREA_WEIGHTS",
    "RiskBand",
    "Rollup",
    "risk_band",
    "roll_up",
    "score_from_binary",
    "score_from_coverage",
]
