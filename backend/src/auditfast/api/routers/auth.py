"""Delegated, read-only sign-in — device-code flow surfaced over HTTP.

``sign-in`` returns the code the auditor enters in a browser; ``complete`` polls for the
token and returns ``{signed_in: false, status: "pending"}`` until they finish, so the UI
can call it on a timer.
"""

from __future__ import annotations

from fastapi import APIRouter

from auditfast import services
from auditfast.api.deps import unwrap
from auditfast.api.schemas import PollRequest

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/sign-in")
async def sign_in() -> dict:
    return unwrap(await services.sign_in())


@router.post("/complete")
async def complete(body: PollRequest) -> dict:
    # A pending poll is a normal 200, not an error — do not unwrap.
    return await services.sign_in_complete(body.poll_seconds)


@router.post("/sign-out")
async def sign_out() -> dict:
    return services.sign_out()
