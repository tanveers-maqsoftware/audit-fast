"""Launch the web UI: ``python -m auditfast_mcp.webapi`` or ``auditfast-web``."""

from __future__ import annotations

import os


def main() -> None:
    import uvicorn

    host = os.environ.get("AUDITFAST_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("AUDITFAST_WEB_PORT", "8080"))
    print(f"AuditFAST web UI -> http://{host}:{port}")
    uvicorn.run("auditfast_mcp.webapi.app:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
