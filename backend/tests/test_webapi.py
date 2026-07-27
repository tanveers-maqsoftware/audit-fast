"""Web API tests — the HTTP front door drives the same flow against a mocked Fabric."""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import test_end_to_end as e2e
from auditfast import services
from auditfast.config import Settings
from auditfast.store.db import Store
from auditfast.webapi import app as webapp
from auditfast.webapi.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
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
    store = Store(tmp_path / "web.sqlite3")
    monkeypatch.setattr(services, "get_settings", lambda: settings)
    monkeypatch.setattr(services, "get_store", lambda: store)
    monkeypatch.setattr(services, "get_authenticator", lambda: e2e.FakeAuthenticator())
    # create_app calls get_settings().ensure_dirs(); keep that pointed at tmp_path too.
    monkeypatch.setattr(webapp, "get_settings", lambda: settings)
    with TestClient(create_app()) as c:
        yield c
    store.close()


def test_index_and_static_are_served(client) -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert "AuditFAST" in page.text
    assert client.get("/static/app.js").status_code == 200


def test_status_reports_signed_in_and_no_write(client) -> None:
    body = client.get("/api/status").json()
    assert body["signed_in_as"] == "auditor@contoso.com"
    assert body["write_capability"].startswith("none")


@respx.mock
def test_full_flow_over_http(client) -> None:
    e2e.mock_fabric(respx.mock)

    started = client.post(
        "/api/engagements",
        json={"workspace_url": e2e.PORTAL_URL, "project_name": "Contoso Orders"},
    ).json()
    eid = started["engagement_id"]

    proposal = client.post(f"/api/engagements/{eid}/discover").json()
    assert proposal["workspace_name"] == "ABC_Orders_PROD"
    assert {a["name"] for a in proposal["artifacts_relevant"]} == {"PL_Load_Orders", "test notebook"}

    confirmed = client.post(f"/api/engagements/{eid}/confirm", json={"confirm": True}).json()
    assert confirmed["confirmed"] is True

    run = client.post(f"/api/engagements/{eid}/run").json()
    assert run["summary"]["artifacts_inspected"] == 2
    by_id = {r["item_id"]: r for r in run["results"]}
    assert by_id["3.1.3"]["score"] == 0  # notebook secret
    assert by_id["11.1.1"]["score"] == 3  # git connected

    report = client.get(f"/api/engagements/{eid}/report").json()
    assert "Contoso Orders" in report["report_markdown"]

    log = client.get(f"/api/engagements/{eid}/audit-log").json()
    assert log["chain_intact"]
    assert all(e["method"] in {"GET", "POST"} for e in log["entries"])


def test_running_before_confirm_is_a_400(client) -> None:
    with respx.mock:
        e2e.mock_fabric(respx.mock)
        started = client.post(
            "/api/engagements",
            json={"workspace_url": e2e.PORTAL_URL, "project_name": "Gate Test"},
        ).json()
        eid = started["engagement_id"]
        client.post(f"/api/engagements/{eid}/discover")

        res = client.post(f"/api/engagements/{eid}/run")
        assert res.status_code == 400
        assert "confirm" in res.json()["detail"]["next_step"].lower()


def test_bad_workspace_url_is_a_400(client) -> None:
    res = client.post(
        "/api/engagements",
        json={"workspace_url": "https://app.fabric.microsoft.com/home", "project_name": "X"},
    )
    assert res.status_code == 400
    assert "error" in res.json()["detail"]
