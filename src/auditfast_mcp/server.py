"""AuditFAST MCP server.

Tool surface, in the order an engagement uses it:

    auditfast_sign_in            -> device code for delegated read-only access
    auditfast_sign_in_complete   -> poll until the token lands
    auditfast_start_engagement   -> workspace URL + project name
    auditfast_discover_workspace -> inventory + proposed scope with rationale
    auditfast_confirm_scope      -> human-in-the-loop gate; nothing runs before this
    auditfast_run_audit          -> deterministic rule engine over the confirmed scope
    auditfast_get_report         -> Markdown report
    auditfast_get_audit_log      -> hash-chained proof of every call made

Every tool returns a dict. Errors come back as ``{"error": ...}`` with a next step
rather than raising, so the calling model can recover in-conversation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

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

mcp = FastMCP("auditfast")


# -- wiring ------------------------------------------------------------------


def _client_for(engagement_id: str | None = None) -> FabricClient:
    """Build a guardrailed Fabric client. This is the only construction path."""
    settings = get_settings()
    store = get_store()
    authenticator = get_authenticator()

    guardrail = Guardrail(
        token_provider=authenticator.get_token,
        settings=settings,
        audit_sink=lambda entry: store.append_audit(entry, engagement_id),
    )
    return FabricClient(guardrail)


def _error(message: str, next_step: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {"error": message}
    if next_step:
        payload["next_step"] = next_step
    return payload


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# -- auth --------------------------------------------------------------------


@mcp.tool()
async def auditfast_sign_in() -> dict:
    """Start read-only sign-in to Microsoft Fabric.

    Returns a device code the auditor enters in a browser. AuditFAST acts as the
    signed-in user, so it can only ever read what that person can already see.
    Follow with auditfast_sign_in_complete.
    """
    authenticator = get_authenticator()
    try:
        existing = authenticator.signed_in_account()
        if existing:
            return {
                "already_signed_in": True,
                "account": existing,
                "next_step": "Call auditfast_start_engagement.",
            }
        flow = await authenticator.begin()
    except AuthError as exc:
        return _error(str(exc))

    return {
        "action_required": "Sign in with the code below, then call auditfast_sign_in_complete.",
        "verification_uri": flow["verification_uri"],
        "user_code": flow["user_code"],
        "expires_in_seconds": flow["expires_in_seconds"],
        "scopes_requested": flow["scopes_requested"],
    }


@mcp.tool()
async def auditfast_sign_in_complete(poll_seconds: int = 60) -> dict:
    """Finish sign-in by polling for the token. Safe to call repeatedly while pending."""
    try:
        return await get_authenticator().complete(poll_seconds=min(max(poll_seconds, 5), 120))
    except AuthError as exc:
        return _error(str(exc), "Call auditfast_sign_in to start a fresh sign-in.")


@mcp.tool()
async def auditfast_sign_out() -> dict:
    """Forget the cached credential."""
    get_authenticator().sign_out()
    return {"signed_out": True}


# -- engagement --------------------------------------------------------------


@mcp.tool()
async def auditfast_start_engagement(
    workspace_url: str, project_name: str, notes: str = ""
) -> dict:
    """Open an engagement against one Fabric workspace.

    Accepts a portal URL (https://app.fabric.microsoft.com/groups/<guid>/...), a bare
    workspace GUID, or a workspace display name. Nothing is read from Fabric yet.
    """
    if not project_name.strip():
        return _error("project_name is required.")

    try:
        ref = parse_workspace_url(workspace_url)
    except ValueError as exc:
        return _error(str(exc))

    store = get_store()
    engagement_id = store.create_engagement(project_name.strip(), workspace_url.strip(), notes)

    resolved_note = ""
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
        "next_step": f"Call auditfast_discover_workspace('{engagement_id}').",
    }


@mcp.tool()
async def auditfast_list_engagements() -> dict:
    """List engagements held in the local store."""
    return {"engagements": get_store().list_engagements()}


# -- discovery ---------------------------------------------------------------


@mcp.tool()
async def auditfast_discover_workspace(engagement_id: str) -> dict:
    """Read the workspace inventory and propose what to audit.

    Read-only: lists items and workspace properties, no definitions and no writes.
    Returns the artifacts judged relevant (with the reason each was included or
    excluded) and the checks that would run. Nothing is audited until
    auditfast_confirm_scope is called.
    """
    store = get_store()
    settings = get_settings()

    engagement = store.get_engagement(engagement_id)
    if engagement is None:
        return _error(f"Unknown engagement {engagement_id!r}.", "Call auditfast_list_engagements.")

    client = _client_for(engagement_id)

    workspace_id = engagement.get("workspace_id")
    try:
        if not workspace_id:
            ref = parse_workspace_url(engagement["workspace_url"])
            match = await client.resolve_workspace_by_name(ref.workspace_name or "")
            if match is None:
                return _error(
                    f"No workspace named {ref.workspace_name!r} is visible to the signed-in "
                    "account.",
                    "Check the name, or pass the workspace URL from the Fabric portal.",
                )
            workspace_id = match["id"]
            store.update_engagement(engagement_id, workspace_id=workspace_id)

        workspace = await discover_inventory(client, workspace_id)
    except GuardrailRejection as exc:
        return _error(f"Guardrail blocked a discovery call: {exc}")
    except EvidenceUnavailable as exc:
        return _error(
            str(exc),
            "Confirm the signed-in account has at least Viewer access to this workspace.",
        )
    except AuthError as exc:
        return _error(str(exc), "Call auditfast_sign_in.")
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
    payload["next_step"] = (
        f"Review the proposal, then call auditfast_confirm_scope('{engagement_id}', "
        "confirm=True) — optionally with exclude_artifact_ids or exclude_item_ids."
    )
    return payload


# -- confirmation gate -------------------------------------------------------


@mcp.tool()
async def auditfast_confirm_scope(
    engagement_id: str,
    confirm: bool = False,
    exclude_artifact_ids: list[str] | None = None,
    include_artifact_ids: list[str] | None = None,
    exclude_item_ids: list[str] | None = None,
) -> dict:
    """Confirm what will be audited. This gate cannot be skipped.

    By default the proposed scope is taken as-is. Pass exclude_artifact_ids to drop
    artifacts, include_artifact_ids to add ones the proposal excluded, and
    exclude_item_ids to drop specific checklist items.
    """
    store = get_store()
    engagement = store.get_engagement(engagement_id)
    if engagement is None:
        return _error(f"Unknown engagement {engagement_id!r}.")
    if engagement.get("status") not in {"discovered", "scoped", "audited"}:
        return _error(
            "Nothing has been discovered for this engagement yet.",
            f"Call auditfast_discover_workspace('{engagement_id}').",
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
            "next_step": (
                "This is a dry run. Re-call with confirm=True to freeze this scope and "
                "unlock auditfast_run_audit."
            ),
        }

    if not in_scope_checks:
        return _error(
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
        "next_step": f"Call auditfast_run_audit('{engagement_id}').",
    }


# -- the audit ---------------------------------------------------------------


@mcp.tool()
async def auditfast_run_audit(engagement_id: str) -> dict:
    """Run the confirmed checks against the workspace and score them.

    Read-only throughout. Every check is a deterministic rule over collected evidence,
    so the same workspace state always produces the same score.
    """
    store = get_store()
    settings = get_settings()

    engagement = store.get_engagement(engagement_id)
    if engagement is None:
        return _error(f"Unknown engagement {engagement_id!r}.")
    if not engagement.get("scope_confirmed_at"):
        return _error(
            "Scope has not been confirmed for this engagement.",
            f"Call auditfast_confirm_scope('{engagement_id}', confirm=True) first.",
        )

    artifact_ids, item_ids = store.get_confirmed_scope(engagement_id)
    checks = [check for item_id in item_ids if (check := get_check(item_id)) is not None]
    if not checks:
        return _error("The confirmed scope resolved to zero known checks.")

    workspace_id = engagement["workspace_id"]
    client = _client_for(engagement_id)
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
        return _error(f"Guardrail blocked an evidence call: {exc}")
    except (EvidenceUnavailable, AuthError) as exc:
        store.finish_run(run_id, {"error": str(exc)}, status="failed")
        return _error(str(exc))
    finally:
        await client.aclose()

    results = run_rules(checks, bundle, settings)
    payload = [r.to_dict() for r in results]

    rollup = roll_up(
        [
            ScoredItem(
                item_id=r.item_id,
                area=r.area,
                category=r.category,
                pillar=r.pillar,
                score=r.score,
                status=r.status,
            )
            for r in results
        ]
    )

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
        "next_step": f"Call auditfast_get_report('{engagement_id}') for the written report.",
    }


# -- outputs -----------------------------------------------------------------


@mcp.tool()
async def auditfast_get_report(engagement_id: str, save_to_file: bool = True) -> dict:
    """Render the Markdown audit report for the latest run."""
    store = get_store()
    settings = get_settings()

    engagement = store.get_engagement(engagement_id)
    if engagement is None:
        return _error(f"Unknown engagement {engagement_id!r}.")

    run = store.latest_run(engagement_id)
    if run is None or run["status"] != "complete":
        return _error(
            "No completed audit run for this engagement.",
            f"Call auditfast_run_audit('{engagement_id}').",
        )

    results = store.get_results(run["id"])
    summary = run["summary_json"]
    import json  # local: only the report path needs it

    summary_data = json.loads(summary) if summary else {}

    rollup = roll_up(
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
        filename = f"{engagement_id}_{run['id']}.md"
        path = settings.reports_dir / filename
        path.write_text(markdown, encoding="utf-8")
        saved_path = str(path)

    return {
        "engagement_id": engagement_id,
        "run_id": run["id"],
        "saved_to": saved_path,
        "report_markdown": markdown,
    }


@mcp.tool()
async def auditfast_get_audit_log(engagement_id: str = "", limit: int = 100) -> dict:
    """Return the hash-chained log of every Fabric call this server made.

    This is the read-only proof: each entry records the guardrail decision, the method,
    and the URL, and the chain is verified on read.
    """
    store = get_store()
    intact, message = store.verify_audit_chain()
    return {
        "chain_intact": intact,
        "chain_status": message,
        "entries": store.get_audit_log(engagement_id or None, limit=min(max(limit, 1), 500)),
    }


@mcp.tool()
async def auditfast_status() -> dict:
    """Server configuration and sign-in state — start here when something is not working."""
    settings = get_settings()
    authenticator = get_authenticator()
    try:
        account = authenticator.signed_in_account()
    except AuthError as exc:
        account = None
        client_error = str(exc)
    else:
        client_error = ""

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


@mcp.tool()
async def auditfast_list_checks() -> dict:
    """List the checks in the MVP catalog with their areas, pillars, and rules."""
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


def run() -> None:
    get_settings().ensure_dirs()
    mcp.run()
