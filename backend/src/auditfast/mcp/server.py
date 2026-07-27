"""AuditFAST MCP server — the MCP front door over the shared engine.

The orchestration lives in ``services.py`` so the web API (``webapi/``) runs the same
code. These tools are thin wrappers that give the engine an MCP tool contract.

Tool sequence, in the order an engagement uses it:

    auditfast_sign_in            -> device code for delegated read-only access
    auditfast_sign_in_complete   -> poll until the token lands
    auditfast_start_engagement   -> workspace URL + project name
    auditfast_discover_workspace -> inventory + proposed scope with rationale
    auditfast_confirm_scope      -> human-in-the-loop gate; nothing runs before this
    auditfast_run_audit          -> deterministic rule engine over the confirmed scope
    auditfast_get_report         -> Markdown report
    auditfast_get_audit_log      -> hash-chained proof of every call made
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from auditfast import services
from auditfast.config import get_settings
from auditfast.db import init_db

mcp = FastMCP("auditfast")


@mcp.tool()
async def auditfast_sign_in() -> dict:
    """Start read-only sign-in to Microsoft Fabric.

    Returns a device code the auditor enters in a browser. AuditFAST acts as the
    signed-in user, so it can only ever read what that person can already see.
    Follow with auditfast_sign_in_complete.
    """
    return await services.sign_in()


@mcp.tool()
async def auditfast_sign_in_complete(poll_seconds: int = 60) -> dict:
    """Finish sign-in by polling for the token. Safe to call repeatedly while pending."""
    return await services.sign_in_complete(poll_seconds)


@mcp.tool()
async def auditfast_sign_out() -> dict:
    """Forget the cached credential."""
    return services.sign_out()


@mcp.tool()
async def auditfast_start_engagement(
    workspace_url: str, project_name: str, notes: str = ""
) -> dict:
    """Open an engagement against one Fabric workspace.

    Accepts a portal URL (https://app.fabric.microsoft.com/groups/<guid>/...), a bare
    workspace GUID, or a workspace display name. Nothing is read from Fabric yet.
    """
    return services.start_engagement(workspace_url, project_name, notes)


@mcp.tool()
async def auditfast_list_engagements() -> dict:
    """List engagements held in the local store."""
    return services.list_engagements()


@mcp.tool()
async def auditfast_discover_workspace(engagement_id: str) -> dict:
    """Read the workspace inventory and propose what to audit.

    Read-only: lists items and workspace properties, no definitions and no writes.
    Returns the artifacts judged relevant (with the reason each was included or
    excluded) and the checks that would run. Nothing is audited until
    auditfast_confirm_scope is called.
    """
    return await services.discover_workspace(engagement_id)


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
    return services.confirm_scope(
        engagement_id,
        confirm=confirm,
        exclude_artifact_ids=exclude_artifact_ids,
        include_artifact_ids=include_artifact_ids,
        exclude_item_ids=exclude_item_ids,
    )


@mcp.tool()
async def auditfast_run_audit(engagement_id: str) -> dict:
    """Run the confirmed checks against the workspace and score them.

    Read-only throughout. Every check is a deterministic rule over collected evidence,
    so the same workspace state always produces the same score.
    """
    return await services.run_audit(engagement_id)


@mcp.tool()
async def auditfast_get_report(engagement_id: str, save_to_file: bool = True) -> dict:
    """Render the Markdown audit report for the latest run."""
    return services.get_report(engagement_id, save_to_file=save_to_file)


@mcp.tool()
async def auditfast_get_audit_log(engagement_id: str = "", limit: int = 100) -> dict:
    """Return the hash-chained log of every Fabric call this server made.

    This is the read-only proof: each entry records the guardrail decision, the method,
    and the URL, and the chain is verified on read.
    """
    return services.get_audit_log(engagement_id, limit)


@mcp.tool()
async def auditfast_status() -> dict:
    """Server configuration and sign-in state — start here when something is not working."""
    return services.status()


@mcp.tool()
async def auditfast_list_checks() -> dict:
    """List the checks in the MVP catalog with their areas, pillars, and rules."""
    return services.list_checks()


def run() -> None:
    get_settings().ensure_dirs()
    init_db()
    mcp.run()
