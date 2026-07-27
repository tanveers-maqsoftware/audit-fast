"""Shared FastAPI helpers.

Services already return ``{"error", "next_step"}`` on failure; ``unwrap`` turns that
into an HTTP error so the browser sees a proper status code plus the recovery step.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException


def unwrap(result: dict[str, Any], *, status: int = 400) -> dict[str, Any]:
    """Pass a success dict through; raise the service error as an HTTP error."""
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=status, detail=result)
    return result
