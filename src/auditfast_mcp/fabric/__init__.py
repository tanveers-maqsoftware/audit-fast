"""Fabric REST access — every method routes through the guardrail."""

from auditfast_mcp.fabric.client import FabricClient
from auditfast_mcp.fabric.urls import ParsedWorkspaceRef, parse_workspace_url

__all__ = ["FabricClient", "ParsedWorkspaceRef", "parse_workspace_url"]
