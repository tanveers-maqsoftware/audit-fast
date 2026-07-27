"""End-to-end flow against a mocked Fabric API.

Proves the handshake the auditor actually performs: start an engagement from a
workspace URL and project name, discover what is there, confirm the proposed scope,
run the audit, and read the report — with every HTTP call intercepted so the test
never touches a real tenant.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest
import respx

from auditfast import server, services
from auditfast.config import Settings
from auditfast.store.db import Store

WORKSPACE_ID = "11111111-2222-3333-4444-555555555555"
PIPELINE_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
NOTEBOOK_ID = "99999999-8888-7777-6666-555555555555"
PORTAL_URL = f"https://app.fabric.microsoft.com/groups/{WORKSPACE_ID}/list"
API = "https://api.fabric.microsoft.com/v1"


class FakeAuthenticator:
    async def get_token(self) -> str:
        return "fake-token"

    def signed_in_account(self) -> str:
        return "auditor@contoso.com"


def _part(path: str, payload: dict | str) -> dict:
    raw = json.dumps(payload) if isinstance(payload, dict) else payload
    return {"path": path, "payload": base64.b64encode(raw.encode()).decode()}


PIPELINE_BODY = {
    "parameters": {"loadDate": {"type": "string"}},
    "activities": [
        {
            "name": "CopyOrders",
            "type": "Copy",
            "description": "Copy orders into Bronze.",
            "policy": {"retry": 3, "retryIntervalInSeconds": 30},
            "typeProperties": {"source": {"path": "@pipeline().parameters.sourcePath"}},
        }
    ],
}

NOTEBOOK_BODY = {
    "cells": [
        {"cell_type": "code", "source": ["load_date = ''"], "metadata": {"tags": ["parameters"]}},
        {
            "cell_type": "code",
            "source": ["conn = 'Server=sql-prod;Password=Hunter2;'"],
            "metadata": {"tags": []},
        },
    ]
}


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Point the server at a temp store, fake auth, and definition reads enabled."""
    settings = Settings(
        client_id="test-client",
        tenant_id="test-tenant",
        data_dir=tmp_path,
        enable_definition_reads=True,
        pipeline_name_pattern=r"^PL_[A-Za-z0-9_]+$",
        notebook_name_pattern=r"^NB_[A-Za-z0-9_]+$",
        workspace_name_pattern=r"^[A-Z]{3}_[A-Za-z]+_(DEV|TEST|PROD)$",
    )
    settings.ensure_dirs()
    store = Store(tmp_path / "e2e.sqlite3")

    monkeypatch.setattr(services, "get_settings", lambda: settings)
    monkeypatch.setattr(services, "get_store", lambda: store)
    monkeypatch.setattr(services, "get_authenticator", lambda: FakeAuthenticator())
    yield settings, store
    store.close()


def mock_fabric(router: respx.Router) -> None:
    router.get(f"{API}/workspaces/{WORKSPACE_ID}").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": WORKSPACE_ID,
                "displayName": "ABC_Orders_PROD",
                "description": "Orders analytics.",
                "capacityId": "cap-1",
            },
        )
    )
    router.get(f"{API}/workspaces/{WORKSPACE_ID}/items").mock(
        return_value=httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": PIPELINE_ID,
                        "type": "DataPipeline",
                        "displayName": "PL_Load_Orders",
                        "description": "Loads orders.",
                    },
                    {"id": NOTEBOOK_ID, "type": "Notebook", "displayName": "test notebook"},
                    {"id": "sm-1", "type": "SemanticModel", "displayName": "Orders Model"},
                ]
            },
        )
    )
    router.get(f"{API}/workspaces/{WORKSPACE_ID}/roleAssignments").mock(
        return_value=httpx.Response(
            200,
            json={
                "value": [
                    {"role": "Admin", "principal": {"type": "Group", "displayName": "SG-Admins"}},
                    {
                        "role": "Contributor",
                        "principal": {"type": "ServicePrincipal", "displayName": "spn-etl"},
                    },
                ]
            },
        )
    )
    router.get(f"{API}/workspaces/{WORKSPACE_ID}/git/connection").mock(
        return_value=httpx.Response(
            200,
            json={
                "gitConnectionState": "ConnectedAndInitialized",
                "gitProviderDetails": {"gitProviderType": "AzureDevOps"},
            },
        )
    )
    router.get(f"{API}/deploymentPipelines").mock(
        return_value=httpx.Response(200, json={"value": []})
    )
    router.get(f"{API}/capacities").mock(
        return_value=httpx.Response(
            200,
            json={
                "value": [
                    {"id": "cap-1", "displayName": "Prod F64", "sku": "F64", "state": "Active"}
                ]
            },
        )
    )
    router.post(f"{API}/workspaces/{WORKSPACE_ID}/dataPipelines/{PIPELINE_ID}/getDefinition").mock(
        return_value=httpx.Response(
            200, json={"definition": {"parts": [_part("pipeline-content.json", PIPELINE_BODY)]}}
        )
    )
    router.post(f"{API}/workspaces/{WORKSPACE_ID}/notebooks/{NOTEBOOK_ID}/getDefinition").mock(
        return_value=httpx.Response(
            200, json={"definition": {"parts": [_part("notebook-content.ipynb", NOTEBOOK_BODY)]}}
        )
    )


@respx.mock
async def test_full_engagement_flow(wired) -> None:
    _settings, store = wired
    mock_fabric(respx.mock)

    # 1. Start from a portal URL and a project name.
    started = await server.auditfast_start_engagement(PORTAL_URL, "Contoso Orders Migration")
    engagement_id = started["engagement_id"]
    assert WORKSPACE_ID in started["workspace_reference"]

    # 2. Discover — inventory plus a proposal with a reason for every decision.
    proposal = await server.auditfast_discover_workspace(engagement_id)
    assert proposal["workspace_name"] == "ABC_Orders_PROD"
    assert proposal["inventory_counts"] == {"DataPipeline": 1, "Notebook": 1, "SemanticModel": 1}

    relevant = {a["name"] for a in proposal["artifacts_relevant"]}
    assert relevant == {"PL_Load_Orders", "test notebook"}

    excluded = {a["name"]: a["why_not"] for a in proposal["artifacts_excluded"]}
    assert "CertyFAST" in excluded["Orders Model"]
    assert proposal["checks_in_scope"]

    # 3. Nothing may run before the auditor confirms.
    blocked = await server.auditfast_run_audit(engagement_id)
    assert "error" in blocked
    assert "confirm" in blocked["next_step"].lower()

    # A dry run shows what would happen without freezing anything.
    dry = await server.auditfast_confirm_scope(engagement_id, confirm=False)
    assert dry["confirmed"] is False

    # 4. Confirm, dropping one check to prove the auditor's edits are honoured.
    confirmed = await server.auditfast_confirm_scope(
        engagement_id, confirm=True, exclude_item_ids=["12.3.4"]
    )
    assert confirmed["confirmed"] is True
    assert confirmed["artifacts_in_scope"] == 2

    # 5. Run.
    run = await server.auditfast_run_audit(engagement_id)
    summary = run["summary"]
    assert summary["artifacts_inspected"] == 2
    assert summary["definitions_enabled"] is True
    assert "12.3.4" not in {r["item_id"] for r in run["results"]}

    by_id = {r["item_id"]: r for r in run["results"]}
    # The seeded workspace is genuinely good at these.
    assert by_id["11.1.1"]["score"] == 3  # Git connected
    assert by_id["6.1.2"]["score"] == 3  # group/SPN based access
    assert by_id["2.4.1"]["score"] == 3  # retry configured
    # ...and genuinely bad at these.
    assert by_id["11.2.1"]["score"] == 0  # no deployment pipeline
    assert by_id["3.1.7"]["score"] == 0  # "test notebook" fails the convention
    assert by_id["3.1.3"]["score"] == 0  # password literal in the notebook
    assert "credential-shaped literal" in by_id["3.1.3"]["failing_objects"][0]

    # 6. Report.
    report = await server.auditfast_get_report(engagement_id)
    markdown = report["report_markdown"]
    assert "# AuditFAST Core — Audit Report: Contoso Orders Migration" in markdown
    assert "3.1.3" in markdown
    assert "Rotate any key found in code" in markdown
    assert report["saved_to"].endswith(".md")


@respx.mock
async def test_every_call_is_logged_and_read_only(wired) -> None:
    """The audit log is the proof: no mutating verb ever leaves the process."""
    _settings, store = wired
    mock_fabric(respx.mock)

    started = await server.auditfast_start_engagement(PORTAL_URL, "Log Proof")
    engagement_id = started["engagement_id"]
    await server.auditfast_discover_workspace(engagement_id)
    await server.auditfast_confirm_scope(engagement_id, confirm=True)
    await server.auditfast_run_audit(engagement_id)

    log = await server.auditfast_get_audit_log(engagement_id, limit=500)
    assert log["chain_intact"], log["chain_status"]

    entries = log["entries"]
    assert entries, "no calls were logged"
    assert all(e["decision"] == "approved" for e in entries)
    assert all(e["method"] in {"GET", "POST"} for e in entries)
    # Every POST is a getDefinition read and nothing else.
    assert all(e["url"].endswith("/getDefinition") for e in entries if e["method"] == "POST")


@respx.mock
async def test_definition_checks_report_unavailable_when_reads_are_off(
    tmp_path, monkeypatch
) -> None:
    """With read-only scopes alone, definition checks must not be silently skipped."""
    settings = Settings(
        client_id="c", tenant_id="t", data_dir=tmp_path, enable_definition_reads=False
    )
    settings.ensure_dirs()
    store = Store(tmp_path / "e2e2.sqlite3")
    monkeypatch.setattr(services, "get_settings", lambda: settings)
    monkeypatch.setattr(services, "get_store", lambda: store)
    monkeypatch.setattr(services, "get_authenticator", lambda: FakeAuthenticator())
    mock_fabric(respx.mock)

    started = await server.auditfast_start_engagement(PORTAL_URL, "No Definitions")
    engagement_id = started["engagement_id"]

    proposal = await server.auditfast_discover_workspace(engagement_id)
    assert any("AUDITFAST_ENABLE_DEFINITION_READS" in w for w in proposal["warnings"])

    await server.auditfast_confirm_scope(engagement_id, confirm=True)
    run = await server.auditfast_run_audit(engagement_id)

    by_id = {r["item_id"]: r for r in run["results"]}
    assert by_id["2.4.1"]["status"] == "evidence_unavailable"
    assert by_id["2.4.1"]["score"] is None
    # Checks that need no definition still score.
    assert by_id["11.1.1"]["status"] == "scored"
    assert run["summary"]["items_unavailable"] > 0
    store.close()


async def test_unknown_engagement_is_a_friendly_error(wired) -> None:
    result = await server.auditfast_discover_workspace("eng_does_not_exist")
    assert "error" in result
    assert "List engagements" in result["next_step"]
