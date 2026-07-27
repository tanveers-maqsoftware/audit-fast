"""REST policy: what a read-only Fabric call is allowed to look like.

The policy is an allowlist, twice over — the host must be known, and the
(method, path) pair must match an explicitly permitted shape. Anything not named
here is rejected, so adding a new endpoint is a deliberate, reviewable act.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from auditfast.guardrail.models import Decision, GuardDecision, RestCall

ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "api.fabric.microsoft.com",
        "api.powerbi.com",
    }
)

# GET is the only verb that reads by definition.
_GET_PATH_ALLOWLIST: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"^/v1/workspaces$",
        r"^/v1/workspaces/[0-9a-fA-F-]{36}$",
        r"^/v1/workspaces/[0-9a-fA-F-]{36}/items$",
        r"^/v1/workspaces/[0-9a-fA-F-]{36}/items/[0-9a-fA-F-]{36}$",
        r"^/v1/workspaces/[0-9a-fA-F-]{36}/roleAssignments$",
        r"^/v1/workspaces/[0-9a-fA-F-]{36}/git/connection$",
        r"^/v1/workspaces/[0-9a-fA-F-]{36}/(dataPipelines|notebooks|lakehouses|warehouses)$",
        r"^/v1/capacities$",
        r"^/v1/deploymentPipelines$",
        r"^/v1/deploymentPipelines/[0-9a-fA-F-]{36}/stages$",
    )
)

# The single POST exception. Fabric exposes item definitions only via POST
# `.../getDefinition` — a read dressed as a POST because the response is large. It is
# permitted with an empty body and nothing else; see config.DEFINITION_SCOPES.
_POST_PATH_ALLOWLIST: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"^/v1/workspaces/[0-9a-fA-F-]{36}/"
        r"(items|dataPipelines|notebooks)/[0-9a-fA-F-]{36}/getDefinition$"
    ),
)

# Belt-and-braces: even inside an allowlisted path, these substrings must never appear.
_FORBIDDEN_PATH_TOKENS: tuple[str, ...] = (
    "/updateDefinition",
    "/deleteItem",
    "/cancel",
    "/execute",
    "/jobs/instances",
    "/refreshes",
)

_MUTATING_METHODS: frozenset[str] = frozenset({"PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})


def validate_rest(call: RestCall) -> GuardDecision:
    """Approve or reject a described REST call. Never performs I/O."""
    method = call.method.upper()
    parsed = urlparse(call.url)

    if parsed.scheme != "https":
        return GuardDecision(Decision.REJECTED, f"non-HTTPS scheme {parsed.scheme!r}", "R1-scheme")

    if parsed.hostname is None or parsed.hostname.lower() not in ALLOWED_HOSTS:
        return GuardDecision(
            Decision.REJECTED, f"host {parsed.hostname!r} is not an allowed Fabric host", "R2-host"
        )

    if method in _MUTATING_METHODS:
        return GuardDecision(
            Decision.REJECTED, f"method {method} can mutate state and is never issued", "R3-method"
        )

    path = parsed.path.rstrip("/") or "/"
    for token in _FORBIDDEN_PATH_TOKENS:
        if token.lower() in path.lower():
            return GuardDecision(
                Decision.REJECTED, f"path contains forbidden segment {token!r}", "R4-path-token"
            )

    if method == "GET":
        if any(pattern.match(path) for pattern in _GET_PATH_ALLOWLIST):
            return GuardDecision(Decision.APPROVED, "GET on allowlisted read path", "R5-get")
        return GuardDecision(
            Decision.REJECTED, f"GET path {path!r} is not on the read allowlist", "R5-get"
        )

    if method == "POST":
        if any(pattern.match(path) for pattern in _POST_PATH_ALLOWLIST):
            return GuardDecision(
                Decision.APPROVED, "POST on the getDefinition read exception", "R6-post-definition"
            )
        return GuardDecision(
            Decision.REJECTED,
            f"POST path {path!r} is not the getDefinition read exception",
            "R6-post-definition",
        )

    return GuardDecision(Decision.REJECTED, f"method {method} is not supported", "R3-method")
