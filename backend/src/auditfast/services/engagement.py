"""Engagement orchestration — the single engine behind every front door.

Both adapters (``auditfast.api`` and ``auditfast.mcp``) call these functions. Each
returns a plain dict; errors come back as ``{"error", "next_step"}`` rather than
raising, so any caller can relay the problem and the recovery step.

Nothing here knows about HTTP or MCP. Persistence is reached only through repositories
(``auditfast.db``); the read-only guarantee lives one layer down, in the guardrail.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from auditfast.auth import AuthError, get_authenticator
from auditfast.catalog.loader import get_check, load_catalog
from auditfast.config import get_settings
from auditfast.db import (
    AuditLogRepository,
    CustomCheckRepository,
    EngagementRepository,
    RunRepository,
    unit_of_work,
)
from auditfast.fabric.client import FabricClient
from auditfast.fabric.urls import parse_workspace_url
from auditfast.guardrail.core import EvidenceUnavailable, Guardrail, GuardrailRejection
from auditfast.inspectors.registry import collect_evidence, discover_inventory
from auditfast.report import render_report
from auditfast.rules.engine import run_rules
from auditfast.scope import propose_scope
from auditfast.scoring.rubric import ScoredItem, roll_up


# -- wiring ------------------------------------------------------------------


def _append_audit(entry: dict, engagement_id: str | None) -> None:
    """Audit sink for the guardrail.

    Uses its own short session that commits per entry, so the immutable call log
    persists even if the surrounding service transaction later rolls back.
    """
    with unit_of_work() as session:
        AuditLogRepository(session).append(entry, engagement_id)


def client_for(engagement_id: str | None = None) -> FabricClient:
    """Build a guardrailed Fabric client. The only client-construction path."""
    guardrail = Guardrail(
        token_provider=get_authenticator().get_token,
        settings=get_settings(),
        audit_sink=lambda entry: _append_audit(entry, engagement_id),
    )
    return FabricClient(guardrail)


def error(message: str, next_step: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {"error": message}
    if next_step:
        payload["next_step"] = next_step
    return payload


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _rollup_from_results(results: list[dict]) -> Any:
    return roll_up(
        [
            ScoredItem(
                item_id=r["item_id"],
                area=r["area"],
                category=r["category"],
                pillar=r["pillar"],
                score=r["score"],
                status=r["status"],
            )
            for r in results
        ]
    )


def _summarize(results: list[dict], *, artifacts_inspected: int, definitions_enabled: bool,
               collection_errors: list[str]) -> dict:
    rollup = _rollup_from_results(results)
    return {
        "overall_score": rollup.overall,
        "risk_band": rollup.band.value,
        "areas": rollup.areas,
        "pillars": rollup.pillars,
        "categories": rollup.categories,
        "items_scored": rollup.items_scored,
        "items_not_applicable": rollup.items_not_applicable,
        "items_unavailable": rollup.items_unavailable,
        "findings": sum(
            1 for r in results if r["status"] == "scored" and (r["score"] or 0) <= 1
        ),
        "manual_pending": sum(1 for r in results if r["status"] == "manual"),
        "artifacts_inspected": artifacts_inspected,
        "definitions_enabled": definitions_enabled,
        "collection_errors": collection_errors,
    }


# -- auth --------------------------------------------------------------------


async def sign_in() -> dict:
    authenticator = get_authenticator()
    try:
        existing = authenticator.signed_in_account()
        if existing:
            return {"already_signed_in": True, "account": existing,
                    "next_step": "Start an engagement."}
        flow = await authenticator.begin()
    except AuthError as exc:
        return error(str(exc))

    return {
        "action_required": "Sign in with the code below, then complete sign-in.",
        "verification_uri": flow["verification_uri"],
        "user_code": flow["user_code"],
        "expires_in_seconds": flow["expires_in_seconds"],
        "scopes_requested": flow["scopes_requested"],
    }


async def sign_in_complete(poll_seconds: int = 60) -> dict:
    try:
        return await get_authenticator().complete(poll_seconds=min(max(poll_seconds, 5), 120))
    except AuthError as exc:
        return error(str(exc), "Start a fresh sign-in.")


def sign_out() -> dict:
    get_authenticator().sign_out()
    return {"signed_out": True}


def status() -> dict:
    settings = get_settings()
    authenticator = get_authenticator()
    try:
        account = authenticator.signed_in_account()
        client_error = ""
    except AuthError as exc:
        account = None
        client_error = str(exc)

    return {
        "signed_in_as": account,
        "client_id_configured": bool(settings.client_id),
        "tenant": settings.tenant_id,
        "configuration_error": client_error,
        "definition_reads_enabled": settings.enable_definition_reads,
        "data_dir": str(settings.data_dir),
        "checks_in_catalog": len(load_catalog()),
        "limits": {
            "request_timeout_seconds": settings.request_timeout_seconds,
            "max_concurrent_calls": settings.max_concurrent_calls,
            "max_items_inspected": settings.max_items_inspected,
        },
        "write_capability": "none — the guardrail exposes no write path on any protocol",
    }


def list_checks() -> dict:
    return {
        "checks": [
            {
                "item_id": c.item_id,
                "area": c.area,
                "category": c.category,
                "pillar": c.pillar.value,
                "title": c.title,
                "rule": c.rule,
                "scoring": c.coverage_semantics.value,
                "needs_definition_read": c.requires_definition,
            }
            for c in load_catalog()
        ]
    }


# -- engagement --------------------------------------------------------------


def start_engagement(workspace_url: str, project_name: str, notes: str = "") -> dict:
    if not project_name.strip():
        return error("project_name is required.")
    try:
        ref = parse_workspace_url(workspace_url)
    except ValueError as exc:
        return error(str(exc))

    with unit_of_work() as session:
        repo = EngagementRepository(session)
        engagement = repo.create(project_name.strip(), workspace_url.strip(), notes)
        engagement_id = engagement.id
        if ref.workspace_id:
            repo.update(engagement_id, workspace_id=ref.workspace_id)
            resolved_note = f"Workspace GUID {ref.workspace_id} parsed from the input."
        else:
            resolved_note = (
                f"Input treated as a workspace name ({ref.workspace_name!r}); it will be "
                "resolved against the workspaces the signed-in account can see."
            )

    return {
        "engagement_id": engagement_id,
        "project_name": project_name.strip(),
        "workspace_reference": resolved_note,
        "next_step": "Discover the workspace.",
    }


def list_engagements() -> dict:
    with unit_of_work() as session:
        return {"engagements": [e.as_dict() for e in EngagementRepository(session).list()]}


def get_engagement(engagement_id: str) -> dict | None:
    with unit_of_work() as session:
        engagement = EngagementRepository(session).get(engagement_id)
        return engagement.as_dict() if engagement else None


async def discover_workspace(engagement_id: str) -> dict:
    settings = get_settings()

    with unit_of_work() as session:
        engagement = EngagementRepository(session).get(engagement_id)
        if engagement is None:
            return error(f"Unknown engagement {engagement_id!r}.", "List engagements to find a valid id.")
        engagement_data = engagement.as_dict()

    client = client_for(engagement_id)
    workspace_id = engagement_data.get("workspace_id")
    try:
        if not workspace_id:
            ref = parse_workspace_url(engagement_data["workspace_url"])
            match = await client.resolve_workspace_by_name(ref.workspace_name or "")
            if match is None:
                return error(
                    f"No workspace named {ref.workspace_name!r} is visible to the signed-in "
                    "account.",
                    "Check the name, or pass the workspace URL from the Fabric portal.",
                )
            workspace_id = match["id"]

        workspace = await discover_inventory(client, workspace_id)
    except GuardrailRejection as exc:
        return error(f"Guardrail blocked a discovery call: {exc}")
    except EvidenceUnavailable as exc:
        return error(
            str(exc),
            "Confirm the signed-in account has at least Viewer access to this workspace.",
        )
    except AuthError as exc:
        return error(str(exc), "Sign in again.")
    finally:
        await client.aclose()

    proposal = propose_scope(workspace, engagement_data["project_name"], settings)

    with unit_of_work() as session:
        repo = EngagementRepository(session)
        repo.update(
            engagement_id,
            workspace_id=workspace_id,
            workspace_name=workspace.name,
            status="discovered",
            discovered_at=_now(),
        )
        repo.replace_inventory(
            engagement_id,
            [
                {
                    "artifact_id": a.artifact_id,
                    "artifact_type": a.artifact_type,
                    "item_type": a.item_type,
                    "name": a.name,
                    "included": a.included,
                    "rationale": a.rationale,
                }
                for a in proposal.artifacts
            ],
        )
        custom = [c.as_dict() for c in CustomCheckRepository(session).for_engagement(engagement_id)]

    payload = proposal.to_dict()
    payload["engagement_id"] = engagement_id
    payload["custom_checks"] = custom
    payload["next_step"] = "Review the proposal, then confirm the scope."
    return payload


def confirm_scope(
    engagement_id: str,
    confirm: bool = False,
    exclude_artifact_ids: list[str] | None = None,
    include_artifact_ids: list[str] | None = None,
    exclude_item_ids: list[str] | None = None,
) -> dict:
    excluded_artifacts = set(exclude_artifact_ids or [])
    extra_artifacts = set(include_artifact_ids or [])
    excluded_items = set(exclude_item_ids or [])

    with unit_of_work() as session:
        repo = EngagementRepository(session)
        engagement = repo.get(engagement_id)
        if engagement is None:
            return error(f"Unknown engagement {engagement_id!r}.")
        if engagement.status not in {"discovered", "scoped", "audited"}:
            return error(
                "Nothing has been discovered for this engagement yet.",
                "Discover the workspace first.",
            )

        inventory = repo.get_inventory(engagement_id)
        selected = [
            row.artifact_id
            for row in inventory
            if (row.proposed or row.artifact_id in extra_artifacts)
            and row.artifact_id not in excluded_artifacts
        ]
        selected_set = set(selected)
        selected_types = {row.artifact_type for row in inventory if row.artifact_id in selected_set}
        selected_types.update({"workspace", "git", "capacity"})

        in_scope_checks = [
            check
            for check in load_catalog()
            if (not check.artifact_types or set(check.artifact_types) & selected_types)
            and check.item_id not in excluded_items
        ]
        custom_checks = [
            c
            for c in CustomCheckRepository(session).for_engagement(engagement_id)
            if c.item_id not in excluded_items
        ]

        if not confirm:
            return {
                "confirmed": False,
                "engagement_id": engagement_id,
                "would_audit_artifacts": len(selected),
                "would_run_checks": [c.item_id for c in in_scope_checks],
                "would_include_custom_checks": [c.item_id for c in custom_checks],
                "next_step": "This is a dry run. Confirm to freeze this scope and unlock the audit.",
            }

        if not in_scope_checks and not custom_checks:
            return error(
                "The resulting scope contains no runnable checks.",
                "Widen the scope, add a custom check, or re-run discovery.",
            )

        item_ids = [c.item_id for c in in_scope_checks] + [c.item_id for c in custom_checks]
        repo.set_confirmed_scope(engagement_id, selected, item_ids)
        repo.update(engagement_id, status="scoped", scope_confirmed_at=_now())

    return {
        "confirmed": True,
        "engagement_id": engagement_id,
        "artifacts_in_scope": len(selected),
        "checks_in_scope": len(in_scope_checks),
        "custom_checks_in_scope": len(custom_checks),
        "excluded_artifacts": sorted(excluded_artifacts),
        "excluded_items": sorted(excluded_items),
        "next_step": "Run the audit.",
    }


def _manual_result(check_dict: dict) -> dict:
    """A custom check enters a run as a manual item awaiting an auditor's score."""
    return {
        "item_id": check_dict["item_id"],
        "title": check_dict["title"],
        "area": check_dict["area"],
        "category": check_dict["category"],
        "pillar": check_dict["pillar"],
        "status": "manual",
        "score": None,
        "coverage": None,
        "detail": check_dict.get("rationale") or "User-added check — score it manually.",
        "severity": None,
        "objects_inspected": 0,
        "failing_objects": [],
        "remediation": "",
        "custom": True,
    }


async def run_audit(engagement_id: str) -> dict:
    settings = get_settings()

    with unit_of_work() as session:
        repo = EngagementRepository(session)
        engagement = repo.get(engagement_id)
        if engagement is None:
            return error(f"Unknown engagement {engagement_id!r}.")
        if not engagement.scope_confirmed_at:
            return error("Scope has not been confirmed for this engagement.", "Confirm the scope first.")
        workspace_id = engagement.workspace_id
        artifact_ids, item_ids = repo.get_confirmed_scope(engagement_id)
        custom_by_item = {
            c.item_id: c.as_dict()
            for c in CustomCheckRepository(session).for_engagement(engagement_id)
        }

    checks = [check for item_id in item_ids if (check := get_check(item_id)) is not None]
    custom_in_scope = [custom_by_item[i] for i in item_ids if i in custom_by_item]
    if not checks and not custom_in_scope:
        return error("The confirmed scope resolved to zero known checks.")

    client = client_for(engagement_id)
    with unit_of_work() as session:
        run = RunRepository(session).start(engagement_id)
        run_id = run.id

    try:
        bundle = await collect_evidence(
            client, workspace_id, selected_artifact_ids=set(artifact_ids), settings=settings
        )
    except GuardrailRejection as exc:
        _finish(run_id, {"error": str(exc)}, "blocked")
        return error(f"Guardrail blocked an evidence call: {exc}")
    except (EvidenceUnavailable, AuthError) as exc:
        _finish(run_id, {"error": str(exc)}, "failed")
        return error(str(exc))
    finally:
        await client.aclose()

    results = [r.to_dict() for r in run_rules(checks, bundle, settings)]
    results.extend(_manual_result(c) for c in custom_in_scope)

    summary = _summarize(
        results,
        artifacts_inspected=len(bundle.artifacts),
        definitions_enabled=bundle.definitions_enabled,
        collection_errors=bundle.workspace.collection_errors,
    )

    with unit_of_work() as session:
        RunRepository(session).save_results(run_id, engagement_id, results)
        RunRepository(session).finish(run_id, summary)
        EngagementRepository(session).update(engagement_id, status="audited")

    return {
        "engagement_id": engagement_id,
        "run_id": run_id,
        "summary": summary,
        "results": results,
        "next_step": "Open the report, and score any manual checks.",
    }


def _finish(run_id: str, summary: dict, status: str) -> None:
    with unit_of_work() as session:
        RunRepository(session).finish(run_id, summary, status)


def score_manual_check(engagement_id: str, item_id: str, score: int | None, note: str = "") -> dict:
    """Record an auditor's manual score for a custom checklist item on the latest run.

    ``score`` in 0–3 marks it scored; ``None`` marks it Not Applicable. The summary is
    recomputed and the change is recorded in the audit log.
    """
    if score is not None and score not in (0, 1, 2, 3):
        return error("score must be 0, 1, 2, 3, or null (Not Applicable).")

    with unit_of_work() as session:
        run = RunRepository(session).latest(engagement_id)
        if run is None or run.status != "complete":
            return error("No completed run to score against.", "Run the audit first.")
        results = RunRepository(session).results(run.id)

        target = next((r for r in results if r["item_id"] == item_id), None)
        if target is None or not target.get("custom"):
            return error(f"{item_id!r} is not a manual custom check on this run.")

        target["score"] = score
        target["detail"] = note or target["detail"]
        if score is None:
            target["status"] = "not_applicable"
            target["severity"] = None
        else:
            target["status"] = "scored"
            target["severity"] = "high" if score <= 1 else None

        summary = _summarize(
            results,
            artifacts_inspected=run.summary.get("artifacts_inspected", 0) if run.summary else 0,
            definitions_enabled=run.summary.get("definitions_enabled", False) if run.summary else False,
            collection_errors=run.summary.get("collection_errors", []) if run.summary else [],
        )
        RunRepository(session).save_results(run.id, engagement_id, results)
        RunRepository(session).finish(run.id, summary)
        AuditLogRepository(session).append(
            {"action": "manual_score", "item_id": item_id, "score": score}, engagement_id
        )

    return {"engagement_id": engagement_id, "item_id": item_id, "score": score, "summary": summary}


def get_report(engagement_id: str, save_to_file: bool = True) -> dict:
    settings = get_settings()

    with unit_of_work() as session:
        engagement = EngagementRepository(session).get(engagement_id)
        if engagement is None:
            return error(f"Unknown engagement {engagement_id!r}.")
        engagement_data = engagement.as_dict()
        run = RunRepository(session).latest(engagement_id)
        if run is None or run.status != "complete":
            return error("No completed audit run for this engagement.", "Run the audit first.")
        summary_data = run.summary or {}
        run_id = run.id
        results = RunRepository(session).results(run_id)

    rollup = _rollup_from_results(results)
    markdown = render_report(
        project_name=engagement_data["project_name"],
        workspace_name=engagement_data.get("workspace_name") or "",
        workspace_id=engagement_data.get("workspace_id") or "",
        results=results,
        rollup=rollup,
        definitions_enabled=bool(summary_data.get("definitions_enabled")),
        collection_errors=summary_data.get("collection_errors") or [],
        account=get_authenticator().signed_in_account(),
    )

    saved_path = None
    if save_to_file:
        settings.reports_dir.mkdir(parents=True, exist_ok=True)
        path = settings.reports_dir / f"{engagement_id}_{run_id}.md"
        path.write_text(markdown, encoding="utf-8")
        saved_path = str(path)

    return {
        "engagement_id": engagement_id,
        "run_id": run_id,
        "summary": summary_data,
        "results": results,
        "saved_to": saved_path,
        "report_markdown": markdown,
    }


def get_audit_log(engagement_id: str = "", limit: int = 100) -> dict:
    with unit_of_work() as session:
        repo = AuditLogRepository(session)
        intact, message = repo.verify_chain()
        return {
            "chain_intact": intact,
            "chain_status": message,
            "entries": repo.tail(engagement_id or None, limit=min(max(limit, 1), 500)),
        }


# -- custom checklist items --------------------------------------------------


def add_custom_check(data: dict, engagement_id: str | None = None) -> dict:
    required = {"item_id", "area", "category", "title"}
    missing = required - set(k for k, v in data.items() if str(v).strip() != "")
    if missing:
        return error(f"Missing required fields: {', '.join(sorted(missing))}.")
    try:
        int(data["area"])
    except (TypeError, ValueError):
        return error("area must be an integer 1–13.")

    with unit_of_work() as session:
        if engagement_id and EngagementRepository(session).get(engagement_id) is None:
            return error(f"Unknown engagement {engagement_id!r}.")
        check = CustomCheckRepository(session).add(engagement_id, data)
        return {"custom_check": check.as_dict()}


def list_custom_checks(engagement_id: str | None = None) -> dict:
    with unit_of_work() as session:
        repo = CustomCheckRepository(session)
        checks = repo.for_engagement(engagement_id) if engagement_id else repo.list_all()
        return {"custom_checks": [c.as_dict() for c in checks]}


def delete_custom_check(check_id: str) -> dict:
    with unit_of_work() as session:
        deleted = CustomCheckRepository(session).delete(check_id)
    if not deleted:
        return error(f"Unknown custom check {check_id!r}.")
    return {"deleted": check_id}
