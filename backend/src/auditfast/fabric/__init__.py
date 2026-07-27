"""Fabric REST access — every method routes through the guardrail."""

from auditfast.fabric.client import FabricClient
from auditfast.fabric.urls import ParsedWorkspaceRef, parse_workspace_url

__all__ = ["FabricClient", "ParsedWorkspaceRef", "parse_workspace_url"]
