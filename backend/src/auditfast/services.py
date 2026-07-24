"""Engagement orchestration — the single engine behind every front door.

Both the MCP server (``server.py``) and the web API (``webapi/``) call these
functions. Each returns a plain dict; errors come back as ``{"error", "next_step"}``
rather than raising, so any caller can relay the problem and the recovery step.

Nothing here knows about MCP or HTTP. The read-only guarantee lives one layer down, in
the guardrail — this module only ever *describes* work and hands it over.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from auditfast_mcp.auth import AuthError, get_authenticator
from auditfast_mcp.catalog.loader import get_check, load_catalog
from auditfast_mcp.config import get_settings
from auditfast_mcp.fabric.client import FabricClient
from auditfast_mcp.fabric.urls import parse_workspace_url
from auditfast_mcp.guardrail.core import EvidenceUnavailable, Guardrail, GuardrailRejection
from auditfast_mcp.inspectors.registry import collect_evidence, discover_inventory
from auditfast_mcp.report import render_report
from auditfast_mcp.rules.engine import run_rules
from auditfast_mcp.scope import propose_scope
from auditfast_mcp.scoring.rubric import ScoredItem, roll_up
from auditfast_mcp.store import get_store


# -- wiring ------------------------------------------------------------------


def client_for(engagement_id: str | None = None) -> FabricClient:
    """Build a guardrailed Fabric client. The only client-construction path."""
    settings = get_settings()
    store = get_store()
    authenticator = get_authenticator()

    guardrail = Guardrail(
        token_provider=authenticator.get_token,
        settings=settings,
        audit_sink=lambda entry: store.append_audit(entry, engagement_id),
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


# -- auth --------------------------------------------------------------------


async def sign_in() -> dict:
    authenticator = get_authenticator()
    try:
        existing = authenticator.signed_in_account()
        if existing:
            return {
                "already_signed_in": True,
                "account": existing,
                "next_step": "Start an engagement.",
            }
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

    store = get_store()
    engagement_id = store.create_engagement(project_name.strip(), workspace_url.strip(), notes)

    if ref.workspace_id:
        store.update_engagement(engagement_id, workspace_id=ref.workspace_id)
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
    return {"engagements": get_store().list_engagements()}


def get_engagement(engagement_id: str) -> dict | None:
    return get_store().get_engagement(engagement_id)


async def discover_workspace(engagement_id: str) -> dict:
    store = get_store()
    settings = get_settings()

    engagement = store.get_engagement(engagement_id)
    if engagement is None:
        return error(f"Unknown engagement {engagement_id!r}.", "List engagements to find a valid id.")

    client = client_for(engagement_id)
    workspace_id = engagement.get("workspace_id")
    try:
        if not workspace_id:
            ref = parse_workspace_url(engagement["workspace_url"])
            match = await client.resolve_workspace_by_name(ref.workspace_name or "")
            if match is None:
                return error(
                    f"No workspace named {ref.workspace_name!r} is visible to the signed-in "
                    "account.",
                    "Check the name, or pass the workspace URL from the Fabric portal.",
                )
            workspace_id = match["id"]
            store.update_engagement(engagement_id, workspace_id=workspace_id)

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

    proposal = propose_scope(workspace, engagement["project_name"], settings)

    store.update_engagement(
        engagement_id,
        workspace_id=workspace_id,
        workspace_name=workspace.name,
        status="discovered",
        discovered_at=_now(),
    )
    store.replace_inventory(
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

    payload = proposal.to_dict()
    payload["engagement_id"] = engagement_id
    payload["next_step"] = "Review the proposal, then confirm the scope."
    return payload


def confirm_scope(
    engagement_id: str,
    confirm: bool = False,
    exclude_artifact_ids: list[str] | None = None,
    include_artifact_ids: list[str] | None = None,
    exclude_item_ids: list[str] | None = None,
) -> dict:
    store = get_store()
    engagement = store.get_engagement(engagement_id)
    if engagement is None:
        return error(f"Unknown engagement {engagement_id!r}.")
    if engagement.get("status") not in {"discovered", "scoped", "audited"}:
        return error(
            "Nothing has been discovered for this engagement yet.",
            "Discover the workspace first.",
        )

    inventory = store.get_inventory(engagement_id)
    excluded_artifacts = set(exclude_artifact_ids or [])
    extra_artifacts = set(include_artifact_ids or [])
    excluded_items = set(exclude_item_ids or [])

    selected = [
        row["artifact_id"]
        for row in inventory
        if (row["proposed"] == 1 or row["artifact_id"] in extra_artifacts)
        and row["artifact_id"] not in excluded_artifacts
    ]
    selected_types = {
        row["artifact_type"] for row in inventory if row["artifact_id"] in set(selected)
    }
    selected_types.update({"workspace", "git", "capacity"})

    in_scope_checks = [
        check
        for check in load_catalog()
        if (not check.artifact_types or set(check.artifact_types) & selected_types)
        and check.item_id not in excluded_items
    ]

    if not confirm:
        return {
            "confirmed": False,
            "engagement_id": engagement_id,
            "would_audit_artifacts": len(selected),
            "would_run_checks": [c.item_id for c in in_scope_checks],
            "next_step": "This is a dry run. Confirm to freeze this scope and unlock the audit.",
        }

    if not in_scope_checks:
        return error(
            "The resulting scope contains no runnable checks.",
            "Widen the scope or re-run discovery.",
        )

    store.set_confirmed_scope(engagement_id, selected, [c.item_id for c in in_scope_checks])
    store.update_engagement(engagement_id, status="scoped", scope_confirmed_at=_now())

    return {
        "confirmed": True,
        "engagement_id": engagement_id,
        "artifacts_in_scope": len(selected),
        "checks_in_scope": len(in_scope_checks),
        "excluded_artifacts": sorted(excluded_artifacts),
        "excluded_items": sorted(excluded_items),
        "next_step": "Run the audit.",
    }


async def run_audit(engagement_id: str) -> dict:
    store = get_store()
    settings = get_settings()

    engagement = store.get_engagement(engagement_id)
    if engagement is None:
        return error(f"Unknown engagement {engagement_id!r}.")
    if not engagement.get("scope_confirmed_at"):
        return error(
            "Scope has not been confirmed for this engagement.",
            "Confirm the scope first.",
        )

    artifact_ids, item_ids = store.get_confirmed_scope(engagement_id)
    checks = [check for item_id in item_ids if (check := get_check(item_id)) is not None]
    if not checks:
        return error("The confirmed scope resolved to zero known checks.")

    workspace_id = engagement["workspace_id"]
    client = client_for(engagement_id)
    run_id = store.start_run(engagement_id)

    try:
        bundle = await collect_evidence(
            client,
            workspace_id,
            selected_artifact_ids=set(artifact_ids),
            settings=settings,
        )
    except GuardrailRejection as exc:
        store.finish_run(run_id, {"error": str(exc)}, status="blocked")
        return error(f"Guardrail blocked an evidence call: {exc}")
    except (EvidenceUnavailable, AuthError) as exc:
        store.finish_run(run_id, {"error": str(exc)}, status="failed")
        return error(str(exc))
    finally:
        await client.aclose()

    results = run_rules(checks, bundle, settings)
    payload = [r.to_dict() for r in results]
    rollup = _rollup_from_results(payload)

    summary = {
        "overall_score": rollup.overall,
        "risk_band": rollup.band.value,
        "areas": rollup.areas,
        "pillars": rollup.pillars,
        "categories": rollup.categories,
        "items_scored": rollup.items_scored,
        "items_not_applicable": rollup.items_not_applicable,
        "items_unavailable": rollup.items_unavailable,
        "findings": sum(1 for r in results if r.is_finding),
        "artifacts_inspected": len(bundle.artifacts),
        "definitions_enabled": bundle.definitions_enabled,
        "collection_errors": bundle.workspace.collection_errors,
    }

    store.save_results(run_id, engagement_id, payload)
    store.finish_run(run_id, summary)
    store.update_engagement(engagement_id, status="audited")

    return {
        "engagement_id": engagement_id,
        "run_id": run_id,
        "summary": summary,
        "results": payload,
        "next_step": "Open the report.",
    }


def get_report(engagement_id: str, save_to_file: bool = True) -> dict:
    store = get_store()
    settings = get_settings()

    engagement = store.get_engagement(engagement_id)
    if engagement is None:
        return error(f"Unknown engagement {engagement_id!r}.")

    run = store.latest_run(engagement_id)
    if run is None or run["status"] != "complete":
        return error("No completed audit run for this engagement.", "Run the audit first.")

    results = store.get_results(run["id"])
    summary_data = json.loads(run["summary_json"]) if run["summary_json"] else {}
    rollup = _rollup_from_results(results)

    markdown = render_report(
        project_name=engagement["project_name"],
        workspace_name=engagement.get("workspace_name") or "",
        workspace_id=engagement.get("workspace_id") or "",
        results=results,
        rollup=rollup,
        definitions_enabled=bool(summary_data.get("definitions_enabled")),
        collection_errors=summary_data.get("collection_errors") or [],
        account=get_authenticator().signed_in_account(),
    )

    saved_path = None
    if save_to_file:
        settings.reports_dir.mkdir(parents=True, exist_ok=True)
        path = settings.reports_dir / f"{engagement_id}_{run['id']}.md"
        path.write_text(markdown, encoding="utf-8")
        saved_path = str(path)

    return {
        "engagement_id": engagement_id,
        "run_id": run["id"],
        "summary": summary_data,
        "results": results,
        "saved_to": saved_path,
        "report_markdown": markdown,
    }


def get_audit_log(engagement_id: str = "", limit: int = 100) -> dict:
    store = get_store()
    intact, message = store.verify_audit_chain()
    return {
        "chain_intact": intact,
        "chain_status": message,
        "entries": store.get_audit_log(engagement_id or None, limit=min(max(limit, 1), 500)),
    }
