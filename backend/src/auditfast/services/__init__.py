"""Services — the orchestration layer shared by every front door.

Import the whole module (``from auditfast import services``) and call
``services.<function>``; both the API and the MCP adapter do exactly that.
"""

from auditfast.services.engagement import (
    add_custom_check,
    client_for,
    confirm_scope,
    delete_custom_check,
    discover_workspace,
    error,
    get_audit_log,
    get_engagement,
    get_report,
    list_checks,
    list_custom_checks,
    list_engagements,
    run_audit,
    score_manual_check,
    sign_in,
    sign_in_complete,
    sign_out,
    start_engagement,
    status,
)

__all__ = [
    "add_custom_check",
    "client_for",
    "confirm_scope",
    "delete_custom_check",
    "discover_workspace",
    "error",
    "get_audit_log",
    "get_engagement",
    "get_report",
    "list_checks",
    "list_custom_checks",
    "list_engagements",
    "run_audit",
    "score_manual_check",
    "sign_in",
    "sign_in_complete",
    "sign_out",
    "start_engagement",
    "status",
]
