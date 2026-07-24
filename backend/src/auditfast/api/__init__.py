"""Web front door — a FastAPI service over the same engine the MCP server uses."""

from auditfast_mcp.webapi.app import app, create_app

__all__ = ["app", "create_app"]
