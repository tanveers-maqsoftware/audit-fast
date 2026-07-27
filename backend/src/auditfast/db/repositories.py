"""Repositories — the only code that reads and writes the database.

Each repository wraps one aggregate and takes a live SQLAlchemy ``Session``. Services
compose repositories inside a ``unit_of_work`` (or, for the append-only audit log, use a
dedicated short session so the trail persists independently of the surrounding
transaction).
"""

from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from auditfast.db.models import (
    AuditLogEntry,
    CustomCheck,
    Engagement,
    Finding,
    InventoryItem,
    Run,
    ScopeArtifact,
    ScopeCheck,
    utc_now_iso,
)

GENESIS_HASH = "0" * 64


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class EngagementRepository:
    _MUTABLE = {
        "workspace_id",
        "workspace_name",
        "status",
        "discovered_at",
        "scope_confirmed_at",
        "notes",
    }

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, project_name: str, workspace_url: str, notes: str = "") -> Engagement:
        engagement = Engagement(
            id=_new_id("eng"),
            project_name=project_name,
            workspace_url=workspace_url,
            status="created",
            notes=notes,
        )
        self.session.add(engagement)
        self.session.flush()
        return engagement

    def get(self, engagement_id: str) -> Engagement | None:
        return self.session.get(Engagement, engagement_id)

    def list(self) -> list[Engagement]:
        return list(
            self.session.scalars(select(Engagement).order_by(Engagement.created_at.desc()))
        )

    def update(self, engagement_id: str, **fields: object) -> None:
        engagement = self.get(engagement_id)
        if engagement is None:
            return
        for key, value in fields.items():
            if key in self._MUTABLE:
                setattr(engagement, key, value)
        self.session.flush()

    # -- inventory -----------------------------------------------------------

    def replace_inventory(self, engagement_id: str, artifacts: list[dict]) -> None:
        self.session.execute(
            delete(InventoryItem).where(InventoryItem.engagement_id == engagement_id)
        )
        self.session.add_all(
            InventoryItem(
                engagement_id=engagement_id,
                artifact_id=a["artifact_id"],
                artifact_type=a["artifact_type"],
                item_type=a["item_type"],
                name=a["name"],
                proposed=bool(a["included"]),
                rationale=a.get("rationale", ""),
            )
            for a in artifacts
        )
        self.session.flush()

    def get_inventory(self, engagement_id: str) -> list[InventoryItem]:
        return list(
            self.session.scalars(
                select(InventoryItem)
                .where(InventoryItem.engagement_id == engagement_id)
                .order_by(InventoryItem.item_type, InventoryItem.name)
            )
        )

    # -- confirmed scope -----------------------------------------------------

    def set_confirmed_scope(
        self, engagement_id: str, artifact_ids: list[str], item_ids: list[str]
    ) -> None:
        self.session.execute(
            delete(ScopeArtifact).where(ScopeArtifact.engagement_id == engagement_id)
        )
        self.session.execute(
            delete(ScopeCheck).where(ScopeCheck.engagement_id == engagement_id)
        )
        self.session.add_all(
            ScopeArtifact(engagement_id=engagement_id, artifact_id=a) for a in set(artifact_ids)
        )
        self.session.add_all(
            ScopeCheck(engagement_id=engagement_id, item_id=i) for i in set(item_ids)
        )
        self.session.flush()

    def get_confirmed_scope(self, engagement_id: str) -> tuple[list[str], list[str]]:
        artifacts = list(
            self.session.scalars(
                select(ScopeArtifact.artifact_id).where(
                    ScopeArtifact.engagement_id == engagement_id
                )
            )
        )
        checks = list(
            self.session.scalars(
                select(ScopeCheck.item_id).where(ScopeCheck.engagement_id == engagement_id)
            )
        )
        return artifacts, checks


class RunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def start(self, engagement_id: str) -> Run:
        run = Run(id=_new_id("run"), engagement_id=engagement_id, status="running")
        self.session.add(run)
        self.session.flush()
        return run

    def finish(self, run_id: str, summary: dict, status: str = "complete") -> None:
        run = self.session.get(Run, run_id)
        if run is None:
            return
        run.finished_at = utc_now_iso()
        run.status = status
        run.summary = summary
        self.session.flush()

    def save_results(self, run_id: str, engagement_id: str, results: list[dict]) -> None:
        self.session.execute(delete(Finding).where(Finding.run_id == run_id))
        self.session.add_all(
            Finding(
                run_id=run_id,
                engagement_id=engagement_id,
                item_id=r["item_id"],
                result=r,
            )
            for r in results
        )
        self.session.flush()

    def latest(self, engagement_id: str) -> Run | None:
        return self.session.scalars(
            select(Run)
            .where(Run.engagement_id == engagement_id)
            .order_by(Run.started_at.desc())
            .limit(1)
        ).first()

    def results(self, run_id: str) -> list[dict]:
        return list(
            self.session.scalars(select(Finding.result).where(Finding.run_id == run_id))
        )


class CustomCheckRepository:
    """User-added checklist items."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, engagement_id: str | None, data: dict) -> CustomCheck:
        check = CustomCheck(
            id=_new_id("chk"),
            engagement_id=engagement_id,
            item_id=data["item_id"],
            area=int(data["area"]),
            category=data["category"],
            pillar=data.get("pillar", "Foundation"),
            title=data["title"],
            rationale=data.get("rationale", ""),
            severity_hint=data.get("severity_hint", "medium"),
            default_weight=data.get("default_weight", "normal"),
        )
        self.session.add(check)
        self.session.flush()
        return check

    def get(self, check_id: str) -> CustomCheck | None:
        return self.session.get(CustomCheck, check_id)

    def delete(self, check_id: str) -> bool:
        check = self.get(check_id)
        if check is None:
            return False
        self.session.delete(check)
        self.session.flush()
        return True

    def for_engagement(self, engagement_id: str) -> list[CustomCheck]:
        """Checks scoped to this engagement plus global (engagement_id IS NULL) ones."""
        return list(
            self.session.scalars(
                select(CustomCheck)
                .where(
                    (CustomCheck.engagement_id == engagement_id)
                    | (CustomCheck.engagement_id.is_(None))
                )
                .order_by(CustomCheck.area, CustomCheck.item_id)
            )
        )

    def list_all(self) -> list[CustomCheck]:
        return list(self.session.scalars(select(CustomCheck).order_by(CustomCheck.created_at)))


class AuditLogRepository:
    """Append-only, hash-chained log of every outbound call.

    Chains across the whole table, not per engagement, so the ordering of all calls is
    tamper-evident as a single sequence.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def append(self, entry: dict, engagement_id: str | None = None) -> AuditLogEntry:
        last = self.session.scalars(
            select(AuditLogEntry).order_by(AuditLogEntry.seq.desc()).limit(1)
        ).first()
        prev_hash = last.hash if last else GENESIS_HASH
        ts = utc_now_iso()
        payload = json.dumps(entry, sort_keys=True)
        digest = hashlib.sha256(f"{prev_hash}|{ts}|{payload}".encode()).hexdigest()
        row = AuditLogEntry(
            engagement_id=engagement_id, ts=ts, entry=entry, prev_hash=prev_hash, hash=digest
        )
        self.session.add(row)
        self.session.flush()
        return row

    def tail(self, engagement_id: str | None = None, limit: int = 200) -> list[dict]:
        stmt = select(AuditLogEntry).order_by(AuditLogEntry.seq.desc()).limit(limit)
        if engagement_id:
            stmt = (
                select(AuditLogEntry)
                .where(AuditLogEntry.engagement_id == engagement_id)
                .order_by(AuditLogEntry.seq.desc())
                .limit(limit)
            )
        return [
            {"seq": r.seq, "ts": r.ts, "hash": r.hash[:16], **r.entry}
            for r in self.session.scalars(stmt)
        ]

    def verify_chain(self, limit: int = 5000) -> tuple[bool, str]:
        rows = list(
            self.session.scalars(
                select(AuditLogEntry).order_by(AuditLogEntry.seq.asc()).limit(limit)
            )
        )
        prev = GENESIS_HASH
        for row in rows:
            payload = json.dumps(row.entry, sort_keys=True)
            expected = hashlib.sha256(f"{prev}|{row.ts}|{payload}".encode()).hexdigest()
            if expected != row.hash or row.prev_hash != prev:
                return False, f"Audit chain broken at entry {row.seq}."
            prev = row.hash
        return True, f"Audit chain intact across {len(rows)} entries."
