"""ORM models — the physical schema.

Mirrors the aggregates in the HLD (engagement -> inventory -> scope -> run -> findings,
plus an immutable audit log) and adds ``custom_checks`` for user-added checklist items.
Timestamps are ISO-8601 strings for portability; JSON payloads use SQLAlchemy's JSON
type so they round-trip as Python dicts/lists on both SQLite and Postgres.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from auditfast.db.base import Base


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Engagement(Base):
    """One audit of one workspace for one project."""

    __tablename__ = "engagements"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_name: Mapped[str] = mapped_column(String(255), nullable=False)
    workspace_url: Mapped[str] = mapped_column(Text, nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(String(64))
    workspace_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created")
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=utc_now_iso)
    discovered_at: Mapped[str | None] = mapped_column(String(32))
    scope_confirmed_at: Mapped[str | None] = mapped_column(String(32))
    notes: Mapped[str] = mapped_column(Text, default="")

    inventory: Mapped[list[InventoryItem]] = relationship(
        back_populates="engagement", cascade="all, delete-orphan"
    )
    runs: Mapped[list[Run]] = relationship(
        back_populates="engagement", cascade="all, delete-orphan"
    )

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "project_name": self.project_name,
            "workspace_url": self.workspace_url,
            "workspace_id": self.workspace_id,
            "workspace_name": self.workspace_name,
            "status": self.status,
            "created_at": self.created_at,
            "discovered_at": self.discovered_at,
            "scope_confirmed_at": self.scope_confirmed_at,
            "notes": self.notes,
        }


class InventoryItem(Base):
    """A discovered Fabric item and whether the proposal put it in scope."""

    __tablename__ = "inventory"

    engagement_id: Mapped[str] = mapped_column(
        ForeignKey("engagements.id", ondelete="CASCADE"), primary_key=True
    )
    artifact_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    artifact_type: Mapped[str] = mapped_column(String(48), nullable=False)
    item_type: Mapped[str] = mapped_column(String(48), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    proposed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rationale: Mapped[str] = mapped_column(Text, default="")

    engagement: Mapped[Engagement] = relationship(back_populates="inventory")


class ScopeArtifact(Base):
    """An artifact the auditor confirmed into the audited scope."""

    __tablename__ = "scope_artifacts"

    engagement_id: Mapped[str] = mapped_column(
        ForeignKey("engagements.id", ondelete="CASCADE"), primary_key=True
    )
    artifact_id: Mapped[str] = mapped_column(String(64), primary_key=True)


class ScopeCheck(Base):
    """A checklist item the auditor confirmed into the audited scope."""

    __tablename__ = "scope_checks"

    engagement_id: Mapped[str] = mapped_column(
        ForeignKey("engagements.id", ondelete="CASCADE"), primary_key=True
    )
    item_id: Mapped[str] = mapped_column(String(32), primary_key=True)


class Run(Base):
    """One execution of the confirmed checks."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    engagement_id: Mapped[str] = mapped_column(
        ForeignKey("engagements.id", ondelete="CASCADE"), nullable=False
    )
    started_at: Mapped[str] = mapped_column(String(32), nullable=False, default=utc_now_iso)
    finished_at: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    summary: Mapped[dict | None] = mapped_column(JSON)

    engagement: Mapped[Engagement] = relationship(back_populates="runs")
    findings: Mapped[list[Finding]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class Finding(Base):
    """One scored check result within a run (the whole result payload, not only 0/1s)."""

    __tablename__ = "findings"

    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
    )
    item_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    engagement_id: Mapped[str] = mapped_column(String(40), nullable=False)
    result: Mapped[dict] = mapped_column(JSON, nullable=False)

    run: Mapped[Run] = relationship(back_populates="findings")


class CustomCheck(Base):
    """A user-added checklist item.

    Auditors extend the baseline catalog with engagement-specific checks. These have no
    automated rule, so they are scored manually; ``engagement_id`` is null for a check
    the auditor wants available to every engagement (a personal/org baseline addition).
    """

    __tablename__ = "custom_checks"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    engagement_id: Mapped[str | None] = mapped_column(
        ForeignKey("engagements.id", ondelete="CASCADE")
    )
    item_id: Mapped[str] = mapped_column(String(32), nullable=False)
    area: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(255), nullable=False)
    pillar: Mapped[str] = mapped_column(String(48), nullable=False, default="Foundation")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, default="")
    severity_hint: Mapped[str] = mapped_column(String(16), default="medium")
    default_weight: Mapped[str] = mapped_column(String(16), default="normal")
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=utc_now_iso)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "engagement_id": self.engagement_id,
            "item_id": self.item_id,
            "area": self.area,
            "category": self.category,
            "pillar": self.pillar,
            "title": self.title,
            "rationale": self.rationale,
            "severity_hint": self.severity_hint,
            "default_weight": self.default_weight,
            "created_at": self.created_at,
        }


class AuditLogEntry(Base):
    """One hash-chained record of an outbound call or a manual score change.

    Each row commits to its predecessor's hash, so a deleted or edited row is
    detectable — this is what makes "prove it never wrote" answerable (TAD 5.3).
    """

    __tablename__ = "audit_log"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    engagement_id: Mapped[str | None] = mapped_column(String(40))
    ts: Mapped[str] = mapped_column(String(32), nullable=False)
    entry: Mapped[dict] = mapped_column(JSON, nullable=False)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
