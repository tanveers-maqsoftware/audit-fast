"""Engine and session lifecycle.

One engine per process. Sessions are short-lived: a service call opens one, does its
unit of work, and closes it. The FastAPI dependency in ``api/deps.py`` and the
``unit_of_work`` context manager both draw from ``SessionLocal`` here.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from auditfast.config import Settings, get_settings
from auditfast.db.base import Base

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _build_engine(settings: Settings) -> Engine:
    url = settings.database_url
    connect_args = {}
    if url.startswith("sqlite"):
        # Allow the engine to be shared across threads (uvicorn workers, MCP async).
        connect_args["check_same_thread"] = False
    engine = create_engine(url, connect_args=connect_args, future=True)

    if url.startswith("sqlite"):
        # Enforce foreign keys (off by default in SQLite) so cascades actually cascade.
        @event.listens_for(engine, "connect")
        def _fk_pragma(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


def get_engine(settings: Settings | None = None) -> Engine:
    global _engine
    if _engine is None:
        _engine = _build_engine(settings or get_settings())
    return _engine


def get_sessionmaker(settings: Settings | None = None) -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(settings), autoflush=False, expire_on_commit=False, future=True
        )
    return _SessionLocal


def init_db(settings: Settings | None = None) -> None:
    """Create tables directly from the ORM metadata.

    Used for tests and first-run bootstrap. Production schema changes go through Alembic
    migrations; ``create_all`` is a no-op for tables that already exist.
    """
    cfg = settings or get_settings()
    cfg.ensure_dirs()
    Base.metadata.create_all(get_engine(cfg))


@contextmanager
def unit_of_work(settings: Settings | None = None) -> Iterator[Session]:
    """A committed-on-success, rolled-back-on-error session scope."""
    session = get_sessionmaker(settings)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine() -> None:
    """Drop the cached engine/sessionmaker so a new settings object takes effect (tests)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
