"""MCP adapter — exposes the shared engine as Model Context Protocol tools."""

from auditfast.mcp.server import mcp, run

__all__ = ["mcp", "run"]
