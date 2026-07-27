"""Launch the API: ``python -m auditfast.api`` or the ``auditfast-api`` console script."""

from __future__ import annotations

import os


def main() -> None:
    import uvicorn

    host = os.environ.get("AUDITFAST_API_HOST", "127.0.0.1")
    port = int(os.environ.get("AUDITFAST_API_PORT", "8080"))
    reload = os.environ.get("AUDITFAST_API_RELOAD", "").lower() in {"1", "true", "yes"}
    print(f"AuditFAST API -> http://{host}:{port}  (docs at /docs)")
    uvicorn.run("auditfast.api.app:app", host=host, port=port, reload=reload, log_level="info")


if __name__ == "__main__":
    main()
