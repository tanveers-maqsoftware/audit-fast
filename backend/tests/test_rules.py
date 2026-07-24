"""Rule engine tests — a healthy workspace scores well, a broken one produces findings."""

from __future__ import annotations

import pytest

from auditfast_mcp.catalog.loader import get_check, load_catalog
from auditfast_mcp.rules import notebook_rules, pipeline_rules, workspace_rules
from auditfast_mcp.rules.engine import RULES, evaluate_check, run_rules
from auditfast_mcp.rules.outcome import RuleStatus


def test_every_catalog_check_has_a_rule() -> None:
    """A catalog entry with no implementation would silently never score."""
    missing = [c.item_id for c in load_catalog() if c.rule_id not in RULES]
    assert missing == [], f"catalog checks with no registered rule: {missing}"


def test_catalog_ids_are_well_formed() -> None:
    for check in load_catalog():
        assert check.item_id.split(".")[0] == str(check.area)
        assert check.remediation.strip(), f"{check.item_id} has no remediation text"


# -- workspace rules ---------------------------------------------------------


def test_healthy_workspace_passes_governance_rules(healthy_bundle, settings) -> None:
    assert workspace_rules.workspace_naming_convention(healthy_bundle, settings).passed
    assert workspace_rules.workspace_spn_for_automation(healthy_bundle, settings).passed
    assert workspace_rules.workspace_no_guest_access(healthy_bundle, settings).passed
    assert workspace_rules.workspace_git_connected(healthy_bundle, settings).passed
    assert workspace_rules.workspace_deployment_pipeline(healthy_bundle, settings).passed
    assert workspace_rules.workspace_capacity_assigned(healthy_bundle, settings).passed


def test_failing_workspace_flags_each_gap(failing_bundle, settings) -> None:
    assert not workspace_rules.workspace_naming_convention(failing_bundle, settings).passed
    assert not workspace_rules.workspace_spn_for_automation(failing_bundle, settings).passed
    assert not workspace_rules.workspace_git_connected(failing_bundle, settings).passed
    assert not workspace_rules.workspace_deployment_pipeline(failing_bundle, settings).passed
    assert not workspace_rules.workspace_capacity_assigned(failing_bundle, settings).passed


def test_guest_access_is_detected(failing_bundle, settings) -> None:
    outcome = workspace_rules.workspace_no_guest_access(failing_bundle, settings)
    assert outcome.passed is False
    assert any("Guest Vendor" in obj for obj in outcome.failing_objects)


def test_three_admins_caps_least_privilege_coverage(failing_bundle, settings) -> None:
    outcome = workspace_rules.workspace_least_privilege(failing_bundle, settings)
    assert outcome.coverage is not None and outcome.coverage < 0.4
    assert "break-glass" in outcome.detail


def test_individual_grants_lower_group_coverage(failing_bundle, settings) -> None:
    outcome = workspace_rules.workspace_group_based_access(failing_bundle, settings)
    assert outcome.coverage == 0.0


def test_missing_role_assignments_are_unavailable_not_zero(healthy_bundle, settings) -> None:
    """Absent evidence must not masquerade as a passing or failing score."""
    healthy_bundle.workspace.role_assignments = []
    outcome = workspace_rules.workspace_group_based_access(healthy_bundle, settings)
    assert outcome.status is RuleStatus.EVIDENCE_UNAVAILABLE


def test_scratch_named_items_are_flagged(failing_bundle, settings) -> None:
    outcome = workspace_rules.workspace_no_orphaned_items(failing_bundle, settings)
    assert outcome.coverage == 0.0
    assert len(outcome.failing_objects) == 2


# -- pipeline rules ----------------------------------------------------------


def test_pipeline_rules_pass_on_healthy_definition(healthy_bundle, settings) -> None:
    assert pipeline_rules.pipeline_naming_convention(healthy_bundle, settings).coverage == 1.0
    assert pipeline_rules.pipeline_parameterized(healthy_bundle, settings).coverage == 1.0
    assert pipeline_rules.pipeline_annotations(healthy_bundle, settings).coverage == 1.0
    assert pipeline_rules.pipeline_retry_configured(healthy_bundle, settings).coverage == 1.0
    assert pipeline_rules.pipeline_retry_bounds(healthy_bundle, settings).coverage == 1.0
    assert pipeline_rules.pipeline_failure_paths(healthy_bundle, settings).coverage == 1.0
    assert pipeline_rules.pipeline_failure_notification(healthy_bundle, settings).coverage == 1.0


def test_pipeline_rules_fail_on_broken_definition(failing_bundle, settings) -> None:
    assert pipeline_rules.pipeline_naming_convention(failing_bundle, settings).coverage == 0.0
    assert pipeline_rules.pipeline_parameterized(failing_bundle, settings).coverage == 0.0
    assert pipeline_rules.pipeline_retry_configured(failing_bundle, settings).coverage == 0.0
    assert pipeline_rules.pipeline_failure_notification(failing_bundle, settings).coverage == 0.0


def test_hardcoded_abfss_path_breaks_parameterization(failing_bundle, settings) -> None:
    outcome = pipeline_rules.pipeline_parameterized(failing_bundle, settings)
    assert "hardcoded literal" in outcome.failing_objects[0]


def test_nested_activities_are_walked(settings, healthy_bundle) -> None:
    """A retry gap inside a ForEach must not hide from the check."""
    body = {
        "activities": [
            {
                "name": "ForEachTable",
                "type": "ForEach",
                "typeProperties": {
                    "activities": [{"name": "CopyInner", "type": "Copy", "policy": {}}]
                },
            }
        ]
    }
    healthy_bundle.artifacts[0].definition = {"pipeline-content.json": body}
    outcome = pipeline_rules.pipeline_retry_configured(healthy_bundle, settings)
    assert outcome.coverage == 0.0
    assert "CopyInner" in outcome.failing_objects[0]


def test_definitions_disabled_reports_unavailable(healthy_bundle, settings) -> None:
    healthy_bundle.definitions_enabled = False
    outcome = pipeline_rules.pipeline_retry_configured(healthy_bundle, settings)
    assert outcome.status is RuleStatus.EVIDENCE_UNAVAILABLE
    assert "AUDITFAST_ENABLE_DEFINITION_READS" in outcome.detail


def test_no_pipelines_is_not_applicable(healthy_bundle, settings) -> None:
    healthy_bundle.artifacts = [
        a for a in healthy_bundle.artifacts if a.artifact_type != "pipeline"
    ]
    outcome = pipeline_rules.pipeline_retry_configured(healthy_bundle, settings)
    assert outcome.status is RuleStatus.NOT_APPLICABLE


# -- notebook rules ----------------------------------------------------------


def test_notebook_rules_pass_on_healthy_notebook(healthy_bundle, settings) -> None:
    assert notebook_rules.notebook_naming_convention(healthy_bundle, settings).coverage == 1.0
    assert notebook_rules.notebook_parameterized(healthy_bundle, settings).coverage == 1.0
    assert notebook_rules.notebook_no_hardcoded_secrets(healthy_bundle, settings).coverage == 1.0
    assert notebook_rules.notebook_timeout_configured(healthy_bundle, settings).coverage == 1.0


def test_notebook_secret_is_caught(failing_bundle, settings) -> None:
    outcome = notebook_rules.notebook_no_hardcoded_secrets(failing_bundle, settings)
    assert outcome.coverage == 0.0
    assert "credential-shaped literal" in outcome.failing_objects[0]


# -- engine ------------------------------------------------------------------


@pytest.mark.parametrize("item_id", ["6.1.2", "2.4.1", "3.1.3"])
def test_evaluate_check_scores_end_to_end(item_id, failing_bundle, settings) -> None:
    check = get_check(item_id)
    assert check is not None
    result = evaluate_check(check, failing_bundle, settings)
    assert result.status == "scored"
    assert result.score == 0
    assert result.is_finding
    assert result.severity is not None
    assert result.to_dict()["remediation"]


def test_full_run_over_healthy_workspace_scores_high(healthy_bundle, settings) -> None:
    results = run_rules(list(load_catalog()), healthy_bundle, settings)
    scored = [r for r in results if r.status == "scored"]
    assert scored, "nothing scored"
    assert all(r.score == 3 for r in scored), [
        (r.item_id, r.score, r.detail) for r in scored if r.score != 3
    ]


def test_full_run_over_failing_workspace_produces_findings(failing_bundle, settings) -> None:
    results = run_rules(list(load_catalog()), failing_bundle, settings)
    findings = [r for r in results if r.is_finding]
    assert len(findings) >= 10
    assert all(f.remediation for f in findings)


def test_a_raising_rule_does_not_abort_the_run(healthy_bundle, settings, monkeypatch) -> None:
    def boom(bundle, cfg):
        raise ValueError("synthetic failure")

    monkeypatch.setitem(RULES, "workspace_git_connected", boom)
    results = run_rules(list(load_catalog()), healthy_bundle, settings)
    broken = next(r for r in results if r.item_id == "11.1.1")
    assert broken.status == "evidence_unavailable"
    assert "synthetic failure" in broken.detail
    assert len(results) == len(load_catalog())
