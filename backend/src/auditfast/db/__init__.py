"""Persistence layer — SQLAlchemy models, session lifecycle, and repositories.

This package is the *only* code that touches the database. Services depend on
repositories; nothing above this layer writes SQL.
"""

from auditfast.db.base import Base
from auditfast.db.repositories import (
    AuditLogRepository,
    CustomCheckRepository,
    EngagementRepository,
    RunRepository,
)
from auditfast.db.session import (
    get_engine,
    get_sessionmaker,
    init_db,
    reset_engine,
    unit_of_work,
)

__all__ = [
    "Base",
    "AuditLogRepository",
    "CustomCheckRepository",
    "EngagementRepository",
    "RunRepository",
    "get_engine",
    "get_sessionmaker",
    "init_db",
    "reset_engine",
    "unit_of_work",
]
