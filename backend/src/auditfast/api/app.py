"""FastAPI application.

Each route is a thin adapter over ``services`` — the identical functions the MCP tools
call, so the web UI and an MCP client drive the same engine and can never diverge.
Service errors carry ``{"error", "next_step"}``; the routes surface them as HTTP 4xx so
the browser can show the message and the recovery step.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from auditfast_mcp import __version__, services
from auditfast_mcp.config import get_settings

STATIC_DIR = Path(__file__).parent / "static"


# -- request bodies ----------------------------------------------------------


class StartEngagementBody(BaseModel):
    workspace_url: str
    project_name: str
    notes: str = ""


class ConfirmScopeBody(BaseModel):
    confirm: bool = False
    exclude_artifact_ids: list[str] | None = None
    include_artifact_ids: list[str] | None = None
    exclude_item_ids: list[str] | None = None


class PollBody(BaseModel):
    poll_seconds: int = 15


def _unwrap(result: dict, *, status: int = 400) -> dict:
    """Turn a service ``{"error": ...}`` into an HTTP error; pass success through."""
    if "error" in result:
        raise HTTPException(status_code=status, detail=result)
    return result


def create_app() -> FastAPI:
    get_settings().ensure_dirs()
    app = FastAPI(
        title="AuditFAST",
        version=__version__,
        summary="Read-only Microsoft Fabric workspace auditing.",
    )

    # -- the page ------------------------------------------------------------

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    # -- auth ----------------------------------------------------------------

    @app.get("/api/status")
    async def status() -> dict:
        return services.status()

    @app.post("/api/auth/sign-in")
    async def sign_in() -> dict:
        return _unwrap(await services.sign_in())

    @app.post("/api/auth/complete")
    async def complete(body: PollBody) -> dict:
        # Not _unwrap: "still pending" is a normal 200, not an error.
        return await services.sign_in_complete(body.poll_seconds)

    @app.post("/api/auth/sign-out")
    async def sign_out() -> dict:
        return services.sign_out()

    # -- engagements ---------------------------------------------------------

    @app.get("/api/engagements")
    async def list_engagements() -> dict:
        return services.list_engagements()

    @app.post("/api/engagements")
    async def start_engagement(body: StartEngagementBody) -> dict:
        return _unwrap(
            services.start_engagement(body.workspace_url, body.project_name, body.notes)
        )

    @app.get("/api/engagements/{engagement_id}")
    async def get_engagement(engagement_id: str) -> dict:
        engagement = services.get_engagement(engagement_id)
        if engagement is None:
            raise HTTPException(status_code=404, detail={"error": "Unknown engagement."})
        return engagement

    @app.post("/api/engagements/{engagement_id}/discover")
    async def discover(engagement_id: str) -> dict:
        return _unwrap(await services.discover_workspace(engagement_id))

    @app.post("/api/engagements/{engagement_id}/confirm")
    async def confirm(engagement_id: str, body: ConfirmScopeBody) -> dict:
        return _unwrap(
            services.confirm_scope(
                engagement_id,
                confirm=body.confirm,
                exclude_artifact_ids=body.exclude_artifact_ids,
                include_artifact_ids=body.include_artifact_ids,
                exclude_item_ids=body.exclude_item_ids,
            )
        )

    @app.post("/api/engagements/{engagement_id}/run")
    async def run(engagement_id: str) -> dict:
        return _unwrap(await services.run_audit(engagement_id))

    @app.get("/api/engagements/{engagement_id}/report")
    async def report(engagement_id: str) -> dict:
        return _unwrap(services.get_report(engagement_id, save_to_file=True))

    @app.get("/api/engagements/{engagement_id}/audit-log")
    async def audit_log(engagement_id: str, limit: int = 100) -> dict:
        return services.get_audit_log(engagement_id, limit)

    # -- catalog -------------------------------------------------------------

    @app.get("/api/checks")
    async def checks() -> dict:
        return services.list_checks()

    return app


app = create_app()
