"""Markdown report rendering.

The report is a deterministic render of the results set — no prose is generated, so
two runs over the same evidence produce byte-identical reports (HLD 4, Report
Generator). Structure follows 05-report-template.md, trimmed to what Core can prove.
"""

from __future__ import annotations

from datetime import UTC, datetime

from auditfast.scoring.rubric import AREA_NAMES, AREA_WEIGHTS, Rollup, risk_band

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def render_report(
    *,
    project_name: str,
    workspace_name: str,
    workspace_id: str,
    results: list[dict],
    rollup: Rollup,
    definitions_enabled: bool,
    collection_errors: list[str],
    account: str | None = None,
) -> str:
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    findings = sorted(
        [r for r in results if r["status"] == "scored" and (r["score"] or 0) <= 1],
        key=lambda r: (_SEVERITY_ORDER.get(r.get("severity") or "low", 9), r["item_id"]),
    )
    unavailable = [r for r in results if r["status"] == "evidence_unavailable"]
    not_applicable = [r for r in results if r["status"] == "not_applicable"]

    lines: list[str] = []
    add = lines.append

    add(f"# AuditFAST Core — Audit Report: {project_name}")
    add("")
    add("| Field | Value |")
    add("|-------|-------|")
    add(f"| Project | {project_name} |")
    add(f"| Workspace | {workspace_name} (`{workspace_id}`) |")
    add(f"| Generated | {generated} |")
    if account:
        add(f"| Signed in as | {account} |")
    add("| Audit mode | AuditFAST Core — deterministic rule engine, read-only |")
    add(f"| Definition reads | {'enabled' if definitions_enabled else 'disabled'} |")
    add("")

    # -- executive summary ---------------------------------------------------
    add("## 1. Executive Summary")
    add("")
    add("| Metric | Value |")
    add("|--------|-------|")
    add(f"| **Overall Score** | **{rollup.overall}%** |")
    add(f"| **Risk Rating** | {rollup.band.emoji} **{rollup.band.value}** |")
    add(f"| Checks scored | {rollup.items_scored} |")
    add(f"| Checks N/A | {rollup.items_not_applicable} |")
    add(f"| Checks with no evidence | {rollup.items_unavailable} |")
    add(f"| Findings (score 0-1) | {len(findings)} |")
    add("")
    add(
        f"> Scope note: Core scored areas {rollup.areas_audited} of the 13-area baseline. "
        "The overall score is renormalized across audited areas and is **not** comparable "
        "to a full deep-dive audit score."
    )
    add("")

    # -- pillar scorecard ----------------------------------------------------
    if rollup.pillars:
        add("### 1.1 Pillar Scorecard")
        add("")
        add("| Pillar | Score | Rating |")
        add("|--------|-------|--------|")
        for pillar, score in sorted(rollup.pillars.items(), key=lambda kv: kv[1]):
            band = risk_band(score)
            add(f"| {pillar} | {score}% | {band.emoji} {band.value} |")
        add("")

    # -- area scorecard ------------------------------------------------------
    add("### 1.2 Area Scorecard")
    add("")
    add("| # | Area | Baseline weight | Score | Rating |")
    add("|---|------|-----------------|-------|--------|")
    for area in rollup.areas_audited:
        score = rollup.areas[area]
        band = risk_band(score)
        add(
            f"| {area} | {AREA_NAMES.get(area, '?')} | {AREA_WEIGHTS.get(area, 0)}% | "
            f"{score}% | {band.emoji} {band.value} |"
        )
    add("")

    # -- findings ------------------------------------------------------------
    add("## 2. Findings")
    add("")
    if not findings:
        add("No check scored 0 or 1. Nothing qualifies as a finding in this run.")
        add("")
    else:
        for finding in findings:
            severity = (finding.get("severity") or "medium").upper()
            add(f"### {finding['item_id']} — {finding['title']}")
            add("")
            add(
                f"**Severity:** {severity}  |  **Score:** {finding['score']}/3  |  "
                f"**Area {finding['area']} — {finding['category']}**  |  "
                f"**Pillar:** {finding['pillar']}"
            )
            add("")
            add(f"**Evidence:** {finding['detail']}")
            add("")
            if finding.get("failing_objects"):
                add("**Objects that failed the check:**")
                add("")
                for obj in finding["failing_objects"]:
                    add(f"- `{obj}`")
                add("")
            if finding.get("remediation"):
                add(f"**Recommended fix:** {finding['remediation'].strip()}")
                add("")

    # -- full results --------------------------------------------------------
    add("## 3. All Checks")
    add("")
    add("| Item | Title | Score | Status | Evidence |")
    add("|------|-------|-------|--------|----------|")
    for result in sorted(results, key=lambda r: [int(p) for p in r["item_id"].split(".")]):
        score = "N/A" if result["score"] is None else f"{result['score']}/3"
        detail = result["detail"].replace("|", "\\|").replace("\n", " ")
        add(
            f"| {result['item_id']} | {result['title']} | {score} | {result['status']} | "
            f"{detail} |"
        )
    add("")

    # -- gaps ----------------------------------------------------------------
    if unavailable or not_applicable or collection_errors:
        add("## 4. Coverage Gaps")
        add("")
        if unavailable:
            add("### 4.1 Evidence Unavailable")
            add("")
            add(
                "These checks were **not** scored. They are reported rather than skipped so "
                "the gap is visible in the score's denominator.")
            add("")
            for result in unavailable:
                add(f"- **{result['item_id']}** {result['title']} — {result['detail']}")
            add("")
        if not_applicable:
            add("### 4.2 Not Applicable")
            add("")
            for result in not_applicable:
                add(f"- **{result['item_id']}** {result['title']} — {result['detail']}")
            add("")
        if collection_errors:
            add("### 4.3 Collection Errors")
            add("")
            for error in collection_errors:
                add(f"- {error}")
            add("")

    add("---")
    add("")
    add(
        "*Generated by AuditFAST Core (MCP server). Every Fabric call in this run was "
        "read-only and validated by the guardrail before execution; the hash-chained call "
        "log is retrievable with `auditfast_get_audit_log`.*"
    )
    return "\n".join(lines)
