"""HTTP adapter — a FastAPI service over the shared engine."""

from auditfast.api.app import app, create_app

__all__ = ["app", "create_app"]
