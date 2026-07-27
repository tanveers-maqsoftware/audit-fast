"""Shared fixtures — synthetic evidence bundles so rules are testable without Fabric."""

from __future__ import annotations

import base64
import json

import pytest

from auditfast.config import Settings
from auditfast.inspectors.base import ArtifactEvidence, EvidenceBundle, WorkspaceEvidence


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        client_id="test-client",
        tenant_id="test-tenant",
        data_dir=tmp_path,
        workspace_name_pattern=r"^[A-Z]{3}_[A-Za-z]+_(DEV|TEST|PROD)$",
        pipeline_name_pattern=r"^PL_[A-Za-z0-9_]+$",
        notebook_name_pattern=r"^NB_[A-Za-z0-9_]+$",
    )


def role(principal_type: str, name: str, role_name: str, upn: str = "") -> dict:
    principal: dict = {"type": principal_type, "displayName": name, "id": name.lower()}
    if upn:
        principal["userDetails"] = {"userPrincipalName": upn}
    return {"role": role_name, "principal": principal}


def pipeline(name: str, body: dict, description: str = "") -> ArtifactEvidence:
    return ArtifactEvidence(
        artifact_id=f"id-{name}",
        artifact_type="pipeline",
        item_type="DataPipeline",
        name=name,
        description=description,
        definition={"pipeline-content.json": body},
    )


def notebook(name: str, cells: list[dict], description: str = "") -> ArtifactEvidence:
    return ArtifactEvidence(
        artifact_id=f"id-{name}",
        artifact_type="notebook",
        item_type="Notebook",
        name=name,
        description=description,
        definition={"notebook-content.ipynb": {"cells": cells}},
    )


def cell(source: str, tags: list[str] | None = None) -> dict:
    return {
        "cell_type": "code",
        "source": [source],
        "metadata": {"tags": tags or []},
    }


def encoded_part(path: str, payload: dict | str) -> dict:
    raw = json.dumps(payload) if isinstance(payload, dict) else payload
    return {"path": path, "payload": base64.b64encode(raw.encode()).decode()}


@pytest.fixture
def healthy_bundle() -> EvidenceBundle:
    """A workspace that should score well across the board."""
    workspace = WorkspaceEvidence(
        workspace_id="11111111-2222-3333-4444-555555555555",
        name="ABC_Sales_PROD",
        description="Production sales analytics workspace.",
        capacity_id="cap-1",
        capacity={"id": "cap-1", "displayName": "Prod F64", "sku": "F64", "state": "Active"},
        role_assignments=[
            role("Group", "SG-Fabric-Admins", "Admin"),
            role("ServicePrincipal", "spn-fabric-etl", "Contributor"),
            role("Group", "SG-Fabric-Readers", "Viewer"),
        ],
        git_connection={
            "gitConnectionState": "ConnectedAndInitialized",
            "gitProviderDetails": {"gitProviderType": "AzureDevOps"},
        },
        deployment_pipelines=[{"displayName": "Sales DTP", "stageCount": 3, "stages": []}],
        items=[
            {"id": "id-PL_Load_Sales", "type": "DataPipeline", "displayName": "PL_Load_Sales"},
            {
                "id": "id-NB_Transform_Sales",
                "type": "Notebook",
                "displayName": "NB_Transform_Sales",
            },
        ],
    )

    good_pipeline = pipeline(
        "PL_Load_Sales",
        {
            "parameters": {"loadDate": {"type": "string"}},
            "activities": [
                {
                    "name": "CopySales",
                    "type": "Copy",
                    "description": "Copy sales rows into Bronze.",
                    "policy": {"retry": 3, "retryIntervalInSeconds": 30},
                },
                {
                    "name": "NotifyFailure",
                    "type": "Teams",
                    "description": "Alert the data team on failure.",
                    "dependsOn": [
                        {"activity": "CopySales", "dependencyConditions": ["Failed"]}
                    ],
                },
            ],
        },
        description="Loads sales data from the source system into Bronze.",
    )

    good_notebook = notebook(
        "NB_Transform_Sales",
        [
            cell("load_date = ''", tags=["parameters"]),
            cell("%%configure\n{ \"sessionTimeout\": 3600 }"),
            cell("df = spark.read.table(table_name)"),
        ],
    )

    return EvidenceBundle(
        workspace=workspace,
        artifacts=[good_pipeline, good_notebook],
        definitions_enabled=True,
    )


@pytest.fixture
def failing_bundle() -> EvidenceBundle:
    """A workspace exhibiting the anti-patterns the checks are meant to catch."""
    workspace = WorkspaceEvidence(
        workspace_id="11111111-2222-3333-4444-555555555555",
        name="my workspace!!",
        description="",
        capacity_id=None,
        capacity=None,
        role_assignments=[
            role("User", "Alice Smith", "Admin", upn="alice@contoso.com"),
            role("User", "Bob Jones", "Admin", upn="bob@contoso.com"),
            role("User", "Carla Diaz", "Admin", upn="carla@contoso.com"),
            role("User", "Guest Vendor", "Member", upn="vendor_ext#EXT#@contoso.com"),
        ],
        git_connection=None,
        deployment_pipelines=[],
        items=[
            {"id": "id-copy of load", "type": "DataPipeline", "displayName": "copy of load"},
            {"id": "id-test_nb", "type": "Notebook", "displayName": "test_nb"},
        ],
    )

    bad_pipeline = pipeline(
        "copy of load",
        {
            "activities": [
                {
                    "name": "Copy1",
                    "type": "Copy",
                    "typeProperties": {
                        "source": {"path": "abfss://raw@prodstorage.dfs.core.windows.net/sales"}
                    },
                }
            ]
        },
    )

    bad_notebook = notebook(
        "test_nb",
        [
            cell(
                "conn = 'Server=sql-prod;Password=Hunter2;'\n"
                "df = spark.read.parquet('abfss://raw@prodstorage.dfs.core.windows.net/x')"
            )
        ],
    )

    return EvidenceBundle(
        workspace=workspace,
        artifacts=[bad_pipeline, bad_notebook],
        definitions_enabled=True,
    )
