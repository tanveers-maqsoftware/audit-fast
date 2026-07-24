"""Workspace-level rules — Areas 1, 6, 11, 12."""

from __future__ import annotations

import re

from auditfast_mcp.config import Settings
from auditfast_mcp.inspectors.base import EvidenceBundle
from auditfast_mcp.rules.outcome import RuleOutcome

# Names that mark an item as scratch, personal, or abandoned.
_SCRATCH_MARKERS = re.compile(
    r"(^|[\s_\-])(test|tst|temp|tmp|old|bkp|backup|copy|dummy|sample|demo|scratch|delete|"
    r"untitled|new item|do not use|dnu)([\s_\-]|$)|copy of|\(\d+\)$",
    re.IGNORECASE,
)

_GUEST_MARKER = "#ext#"
_ELEVATED_ROLES = {"admin", "member"}


def _principal_label(assignment: dict) -> str:
    principal = assignment.get("principal") or {}
    name = principal.get("displayName") or principal.get("id") or "unknown principal"
    role = assignment.get("role", "?")
    return f"{name} ({role})"


def workspace_naming_convention(bundle: EvidenceBundle, settings: Settings) -> RuleOutcome:
    workspace = bundle.workspace
    name = workspace.name or ""
    try:
        matches = bool(re.match(settings.workspace_name_pattern, name))
    except re.error as exc:
        return RuleOutcome.unavailable(f"Invalid workspace name pattern configured: {exc}")

    has_description = bool((workspace.description or "").strip())
    passed = matches and has_description

    reasons = []
    if not matches:
        reasons.append(f"name {name!r} does not match {settings.workspace_name_pattern!r}")
    if not has_description:
        reasons.append("workspace description is empty")

    detail = (
        f"Workspace {name!r} matches the naming convention and carries a description."
        if passed
        else "Workspace organization is not self-describing: " + "; ".join(reasons) + "."
    )
    return RuleOutcome.binary_result(passed, detail, failing=[name] if not passed else [])


def workspace_least_privilege(bundle: EvidenceBundle, settings: Settings) -> RuleOutcome:
    """Flag over-privileged grants, not elevated grants per se.

    A ratio of elevated-to-total roles is the wrong measure: a well-run workspace with
    one Admin *group* and two Viewer groups would score badly on it. What actually
    breaches least privilege is an elevated role held by a named individual, or Admin
    spread past the two break-glass principals.
    """
    assignments = bundle.workspace.role_assignments
    if not assignments:
        return RuleOutcome.unavailable(
            "No role assignments returned — the signed-in account may lack workspace "
            "Admin rights, which are required to read role assignments."
        )

    admins: list[dict] = []
    over_privileged: dict[str, str] = {}

    for assignment in assignments:
        role_name = str(assignment.get("role", "")).lower()
        principal_type = str((assignment.get("principal") or {}).get("type", "")).lower()
        label = _principal_label(assignment)

        if role_name == "admin":
            admins.append(assignment)
        if role_name in _ELEVATED_ROLES and principal_type == "user":
            over_privileged[label] = f"{label} — individual user holds an elevated role"

    # Admin beyond two break-glass principals is excess regardless of principal type.
    excess = max(0, len(admins) - 2)
    if excess:
        for assignment in admins:
            if excess == 0:
                break
            label = _principal_label(assignment)
            if label not in over_privileged:
                over_privileged[label] = f"{label} — Admin beyond the two break-glass grants"
                excess -= 1

    passing = [
        _principal_label(a) for a in assignments if _principal_label(a) not in over_privileged
    ]

    detail = (
        f"{len(passing)} of {len(assignments)} assignments are within least privilege; "
        f"{len(admins)} Admin grant(s) total."
    )
    if len(admins) > 2:
        detail += f" {len(admins)} Admin grants exceeds the two break-glass maximum."

    return RuleOutcome.coverage_result(
        passing=passing,
        failing=list(over_privileged.values()),
        detail=detail,
    )


def workspace_group_based_access(bundle: EvidenceBundle, settings: Settings) -> RuleOutcome:
    assignments = bundle.workspace.role_assignments
    if not assignments:
        return RuleOutcome.unavailable(
            "No role assignments returned — workspace Admin rights are required to read them."
        )

    groups, individuals = [], []
    for assignment in assignments:
        principal_type = str((assignment.get("principal") or {}).get("type", "")).lower()
        if principal_type == "group":
            groups.append(_principal_label(assignment))
        elif principal_type == "user":
            individuals.append(_principal_label(assignment))
        else:
            # Service principals and managed identities are legitimate non-group grants.
            groups.append(_principal_label(assignment))

    return RuleOutcome.coverage_result(
        passing=groups,
        failing=individuals,
        detail=(
            f"{len(groups)} of {len(assignments)} assignments go to groups or service "
            f"identities; {len(individuals)} go to individual user accounts."
        ),
    )


def workspace_spn_for_automation(bundle: EvidenceBundle, settings: Settings) -> RuleOutcome:
    assignments = bundle.workspace.role_assignments
    if not assignments:
        return RuleOutcome.unavailable(
            "No role assignments returned — workspace Admin rights are required to read them."
        )

    automation_types = {"serviceprincipal", "managedidentity", "serviceprincipalprofile"}
    identities = [
        _principal_label(a)
        for a in assignments
        if str((a.get("principal") or {}).get("type", "")).lower() in automation_types
    ]
    passed = bool(identities)
    detail = (
        f"Automation identities hold workspace roles: {', '.join(identities)}."
        if passed
        else "No service principal or managed identity holds a workspace role — scheduled "
        "work is running under personal accounts."
    )
    return RuleOutcome.binary_result(passed, detail)


def workspace_no_guest_access(bundle: EvidenceBundle, settings: Settings) -> RuleOutcome:
    assignments = bundle.workspace.role_assignments
    if not assignments:
        return RuleOutcome.unavailable(
            "No role assignments returned — workspace Admin rights are required to read them."
        )

    guests = [
        _principal_label(a)
        for a in assignments
        if _GUEST_MARKER
        in str(
            (a.get("principal") or {}).get("userDetails", {}).get("userPrincipalName", "")
            or (a.get("principal") or {}).get("displayName", "")
        ).lower()
    ]
    passed = not guests
    detail = (
        "No guest (#EXT#) principals hold a workspace role."
        if passed
        else f"{len(guests)} guest principal(s) hold workspace roles: {', '.join(guests)}."
    )
    return RuleOutcome.binary_result(passed, detail, failing=guests)


def workspace_git_connected(bundle: EvidenceBundle, settings: Settings) -> RuleOutcome:
    connection = bundle.workspace.git_connection
    if connection is None:
        return RuleOutcome.binary_result(
            False,
            "No Git connection is configured for this workspace — item definitions are "
            "not under source control.",
        )

    state = str(connection.get("gitConnectionState", "")).lower()
    passed = state == "connectedandinitialized"
    provider = (connection.get("gitProviderDetails") or {}).get("gitProviderType", "unknown")
    detail = (
        f"Workspace is connected to {provider} source control (state: {state})."
        if passed
        else f"Git connection state is {state or 'not connected'} — source control is not "
        "fully established."
    )
    return RuleOutcome.binary_result(passed, detail)


def workspace_deployment_pipeline(bundle: EvidenceBundle, settings: Settings) -> RuleOutcome:
    pipelines = bundle.workspace.deployment_pipelines
    if not pipelines:
        return RuleOutcome.binary_result(
            False,
            "This workspace is not assigned to any Fabric deployment pipeline — there is "
            "no governed promotion path into production.",
        )

    best = max(pipelines, key=lambda p: p.get("stageCount", 0))
    stage_count = best.get("stageCount", 0)
    passed = stage_count >= 3
    detail = (
        f"Workspace is bound to deployment pipeline {best.get('displayName', '?')!r} "
        f"with {stage_count} stages."
        if passed
        else f"Deployment pipeline {best.get('displayName', '?')!r} has only {stage_count} "
        "stage(s); a Dev/Test/Prod promotion path needs at least three."
    )
    return RuleOutcome.binary_result(passed, detail)


def workspace_capacity_assigned(bundle: EvidenceBundle, settings: Settings) -> RuleOutcome:
    workspace = bundle.workspace
    if not workspace.capacity_id:
        return RuleOutcome.binary_result(
            False,
            "Workspace is not assigned to a Fabric capacity — it is running on shared or "
            "trial compute with no sizing analysis behind it.",
        )
    if workspace.capacity is None:
        return RuleOutcome.unavailable(
            f"Workspace reports capacity {workspace.capacity_id} but it could not be "
            "resolved — the signed-in account may not have access to the capacity."
        )

    sku = (workspace.capacity.get("sku") or "").upper()
    state = str(workspace.capacity.get("state", "")).lower()
    passed = bool(sku) and state == "active"
    detail = (
        f"Workspace runs on capacity {workspace.capacity.get('displayName', '?')!r} "
        f"(SKU {sku}, state {state})."
        if passed
        else f"Capacity {workspace.capacity.get('displayName', '?')!r} is in state "
        f"{state or 'unknown'} with SKU {sku or 'unresolved'}."
    )
    return RuleOutcome.binary_result(passed, detail)


def workspace_no_orphaned_items(bundle: EvidenceBundle, settings: Settings) -> RuleOutcome:
    items = bundle.workspace.items
    if not items:
        return RuleOutcome.not_applicable("The workspace contains no items.")

    clean, scratch = [], []
    for item in items:
        name = item.get("displayName", "")
        label = f"{name} [{item.get('type', '?')}]"
        (scratch if _SCRATCH_MARKERS.search(name) else clean).append(label)

    return RuleOutcome.coverage_result(
        passing=clean,
        failing=scratch,
        detail=(
            f"{len(scratch)} of {len(items)} items carry scratch or personal naming markers. "
            "Name-based proxy — runtime idleness requires the per-item job history API (Phase 2)."
        ),
    )
