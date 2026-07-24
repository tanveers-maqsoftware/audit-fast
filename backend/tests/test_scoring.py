"""Scoring, rollup, URL parsing, and the audit-log chain."""

from __future__ import annotations

import pytest

from auditfast_mcp.fabric.urls import parse_workspace_url
from auditfast_mcp.scoring.rubric import (
    RiskBand,
    ScoredItem,
    risk_band,
    roll_up,
    score_from_binary,
    score_from_coverage,
)
from auditfast_mcp.store.db import Store


@pytest.mark.parametrize(
    ("coverage", "expected"),
    [(1.0, 3), (0.96, 3), (0.95, 3), (0.94, 2), (0.8, 2), (0.79, 1), (0.4, 1), (0.39, 0), (0.0, 0)],
)
def test_coverage_bands(coverage: float, expected: int) -> None:
    assert score_from_coverage(coverage) == expected


def test_binary_scoring() -> None:
    assert score_from_binary(True) == 3
    assert score_from_binary(False) == 0


@pytest.mark.parametrize(
    ("pct", "band"),
    [
        (0, RiskBand.CRITICAL),
        (40, RiskBand.CRITICAL),
        (41, RiskBand.HIGH),
        (60, RiskBand.HIGH),
        (75, RiskBand.MEDIUM),
        (90, RiskBand.GOOD),
        (91, RiskBand.EXCELLENT),
        (100, RiskBand.EXCELLENT),
    ],
)
def test_risk_bands(pct: float, band: RiskBand) -> None:
    assert risk_band(pct) is band


def test_category_score_follows_the_baseline_formula() -> None:
    """01-scoring-rubric.md section 2: (3+2+1) / (3*3) = 66.7%, N/A excluded."""
    items = [
        ScoredItem("1.1.1", 1, "Solution Architecture", "Foundation", 3, "scored"),
        ScoredItem("1.1.2", 1, "Solution Architecture", "Foundation", 2, "scored"),
        ScoredItem("1.1.3", 1, "Solution Architecture", "Foundation", 1, "scored"),
        ScoredItem("1.1.4", 1, "Solution Architecture", "Foundation", None, "not_applicable"),
    ]
    rollup = roll_up(items)
    assert rollup.categories["1 — Solution Architecture"] == pytest.approx(66.7, abs=0.1)
    assert rollup.items_scored == 3
    assert rollup.items_not_applicable == 1


def test_overall_renormalizes_across_audited_areas_only() -> None:
    """A Core scan covering two areas must not be diluted by the eleven it skipped."""
    items = [
        ScoredItem("6.1.1", 6, "IAM", "Security", 3, "scored"),
        ScoredItem("12.1.1", 12, "Capacity Planning", "CostOptimization", 0, "scored"),
    ]
    rollup = roll_up(items)
    # Area 6 weight 12, area 12 weight 7 -> (12*100 + 7*0) / 19
    assert rollup.overall == pytest.approx(63.2, abs=0.1)
    assert rollup.areas_audited == [6, 12]
    assert rollup.pillars["Security"] == 100.0
    assert rollup.pillars["CostOptimization"] == 0.0


def test_pillars_follow_the_check_tag_not_the_area() -> None:
    """A Security check living in Area 3 must drag the Security pillar down.

    Rolling pillars up from areas produced "Security 100%" above a CRITICAL security
    finding, because 3.1.3 sits in Area 3 (PerformanceEfficiency).
    """
    items = [
        ScoredItem("6.1.2", 6, "IAM", "Security", 3, "scored"),
        ScoredItem("3.1.3", 3, "Spark Notebook Quality", "Security", 0, "scored"),
        ScoredItem("3.1.7", 3, "Spark Notebook Quality", "OperationalExcellence", 3, "scored"),
    ]
    rollup = roll_up(items)
    assert rollup.pillars["Security"] == 50.0
    assert rollup.pillars["OperationalExcellence"] == 100.0


def test_unavailable_items_are_counted_but_not_scored() -> None:
    items = [
        ScoredItem("2.4.1", 2, "Error Handling & Retry", "Reliability", 3, "scored"),
        ScoredItem(
            "2.4.3", 2, "Error Handling & Retry", "Reliability", None, "evidence_unavailable"
        ),
    ]
    rollup = roll_up(items)
    assert rollup.items_scored == 1
    assert rollup.items_unavailable == 1
    assert rollup.areas[2] == 100.0


def test_empty_rollup_is_safe() -> None:
    rollup = roll_up([])
    assert rollup.overall == 0.0
    assert rollup.areas == {}


# -- URL parsing -------------------------------------------------------------


def test_portal_url_yields_the_workspace_guid_not_the_item_guid() -> None:
    ref = parse_workspace_url(
        "https://app.fabric.microsoft.com/groups/11111111-2222-3333-4444-555555555555/"
        "pipelines/66666666-7777-8888-9999-000000000000"
    )
    assert ref.workspace_id == "11111111-2222-3333-4444-555555555555"


def test_bare_guid_and_name_are_both_accepted() -> None:
    assert parse_workspace_url("11111111-2222-3333-4444-555555555555").workspace_id
    ref = parse_workspace_url("Sales Analytics PROD")
    assert ref.needs_lookup
    assert ref.workspace_name == "Sales Analytics PROD"


def test_url_without_a_guid_is_rejected_with_guidance() -> None:
    with pytest.raises(ValueError, match="app.fabric.microsoft.com"):
        parse_workspace_url("https://app.fabric.microsoft.com/home")


def test_empty_input_is_rejected() -> None:
    with pytest.raises(ValueError):
        parse_workspace_url("   ")


# -- audit log chain ---------------------------------------------------------


def test_audit_chain_detects_tampering(tmp_path) -> None:
    store = Store(tmp_path / "test.sqlite3")
    for i in range(5):
        store.append_audit(
            {"method": "GET", "url": f"https://x/{i}", "decision": "approved"}, "eng1"
        )

    intact, message = store.verify_audit_chain()
    assert intact, message

    # Rewrite one entry as if someone hid a call.
    store._conn.execute(
        "UPDATE audit_log SET entry_json = ? WHERE seq = 3",
        ('{"method": "DELETE", "url": "https://x/hidden", "decision": "approved"}',),
    )
    store._conn.commit()

    intact, message = store.verify_audit_chain()
    assert not intact
    assert "broken at entry 3" in message
    store.close()
