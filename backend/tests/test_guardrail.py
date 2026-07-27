"""Guardrail security suite — the blocking release gate (TAD section 9).

If any test here fails, the read-only guarantee is not intact and the server must
not be pointed at a client tenant.
"""

from __future__ import annotations

import pytest

from auditfast.guardrail.models import RestCall
from auditfast.guardrail.rest_validator import validate_rest

WORKSPACE = "11111111-2222-3333-4444-555555555555"
ITEM = "66666666-7777-8888-9999-000000000000"
BASE = "https://api.fabric.microsoft.com/v1"


def call(method: str, url: str) -> RestCall:
    return RestCall(method=method, url=url, purpose="test")


# -- every mutating verb is refused ------------------------------------------


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
def test_mutating_methods_are_rejected(method: str) -> None:
    decision = validate_rest(call(method, f"{BASE}/workspaces/{WORKSPACE}/items"))
    assert not decision.approved
    assert decision.rule == "R3-method"


@pytest.mark.parametrize(
    "url",
    [
        f"{BASE}/workspaces/{WORKSPACE}/items/{ITEM}/updateDefinition",
        f"{BASE}/workspaces/{WORKSPACE}/items/{ITEM}/deleteItem",
        f"{BASE}/workspaces/{WORKSPACE}/dataPipelines/{ITEM}/jobs/instances",
        f"{BASE}/workspaces/{WORKSPACE}/notebooks/{ITEM}/execute",
        f"{BASE}/workspaces/{WORKSPACE}/items/{ITEM}/refreshes",
    ],
)
def test_write_and_execute_paths_are_rejected(url: str) -> None:
    """Even as a POST, anything that mutates or triggers work is refused."""
    assert not validate_rest(call("POST", url)).approved


def test_post_is_only_allowed_for_get_definition() -> None:
    approved = validate_rest(
        call("POST", f"{BASE}/workspaces/{WORKSPACE}/dataPipelines/{ITEM}/getDefinition")
    )
    assert approved.approved
    assert approved.rule == "R6-post-definition"

    rejected = validate_rest(call("POST", f"{BASE}/workspaces/{WORKSPACE}/items"))
    assert not rejected.approved


# -- host and scheme ---------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/v1/workspaces",
        "https://api.fabric.microsoft.com.evil.example.com/v1/workspaces",
        "https://management.azure.com/subscriptions",
    ],
)
def test_unknown_hosts_are_rejected(url: str) -> None:
    decision = validate_rest(call("GET", url))
    assert not decision.approved
    assert decision.rule == "R2-host"


def test_plaintext_is_rejected() -> None:
    decision = validate_rest(call("GET", "http://api.fabric.microsoft.com/v1/workspaces"))
    assert not decision.approved
    assert decision.rule == "R1-scheme"


# -- allowlist is exact, not prefix-based ------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        f"{BASE}/workspaces",
        f"{BASE}/workspaces/{WORKSPACE}",
        f"{BASE}/workspaces/{WORKSPACE}/items",
        f"{BASE}/workspaces/{WORKSPACE}/roleAssignments",
        f"{BASE}/workspaces/{WORKSPACE}/git/connection",
        f"{BASE}/capacities",
        f"{BASE}/deploymentPipelines",
    ],
)
def test_read_endpoints_are_approved(url: str) -> None:
    assert validate_rest(call("GET", url)).approved


@pytest.mark.parametrize(
    "url",
    [
        f"{BASE}/admin/workspaces",
        f"{BASE}/workspaces/{WORKSPACE}/users",
        f"{BASE}/workspaces/not-a-guid/items",
        f"{BASE}/workspaces/{WORKSPACE}/items/{ITEM}/dataAccessRoles",
    ],
)
def test_unlisted_read_endpoints_are_rejected(url: str) -> None:
    """A read that nobody reviewed is still a call we do not make."""
    decision = validate_rest(call("GET", url))
    assert not decision.approved
    assert decision.rule == "R5-get"


def test_guardrail_exposes_no_write_method() -> None:
    """L6: the write path must not exist in the code, not merely be unused."""
    from auditfast.guardrail.core import Guardrail

    surface = {name for name in dir(Guardrail) if not name.startswith("_")}
    # aclose only releases pooled connections; it issues no request.
    assert surface == {"validate", "execute", "aclose"}
    for forbidden in ("write", "post", "put", "patch", "delete", "update", "create"):
        assert forbidden not in surface
