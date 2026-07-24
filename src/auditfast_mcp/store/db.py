"""SQLite-backed engagement store.

The schema mirrors the aggregates in HLD section 5 (engagement -> inventory -> scope ->
run -> findings -> audit log) so it ports to the PostgreSQL schema in the TAD without
reshaping. The audit log is hash-chained: each entry commits to its predecessor, so a
deleted or edited row is detectable — this is what makes "prove it never wrote"
answerable (TAD 5.3).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from auditfast_mcp.config import Settings, get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS engagements (
    id                  TEXT PRIMARY KEY,
    project_name        TEXT NOT NULL,
    workspace_url       TEXT NOT NULL,
    workspace_id        TEXT,
    workspace_name      TEXT,
    status              TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    discovered_at       TEXT,
    scope_confirmed_at  TEXT,
    notes               TEXT
);

CREATE TABLE IF NOT EXISTS inventory (
    engagement_id   TEXT NOT NULL,
    artifact_id     TEXT NOT NULL,
    artifact_type   TEXT NOT NULL,
    item_type       TEXT NOT NULL,
    name            TEXT NOT NULL,
    proposed        INTEGER NOT NULL,
    rationale       TEXT,
    PRIMARY KEY (engagement_id, artifact_id)
);

CREATE TABLE IF NOT EXISTS scope_artifacts (
    engagement_id   TEXT NOT NULL,
    artifact_id     TEXT NOT NULL,
    PRIMARY KEY (engagement_id, artifact_id)
);

CREATE TABLE IF NOT EXISTS scope_checks (
    engagement_id   TEXT NOT NULL,
    item_id         TEXT NOT NULL,
    PRIMARY KEY (engagement_id, item_id)
);

CREATE TABLE IF NOT EXISTS runs (
    id              TEXT PRIMARY KEY,
    engagement_id   TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL,
    summary_json    TEXT
);

CREATE TABLE IF NOT EXISTS findings (
    run_id          TEXT NOT NULL,
    engagement_id   TEXT NOT NULL,
    item_id         TEXT NOT NULL,
    result_json     TEXT NOT NULL,
    PRIMARY KEY (run_id, item_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    engagement_id   TEXT,
    ts              TEXT NOT NULL,
    entry_json      TEXT NOT NULL,
    prev_hash       TEXT NOT NULL,
    hash            TEXT NOT NULL
);
"""

GENESIS_HASH = "0" * 64


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # -- engagements ----------------------------------------------------------

    def create_engagement(
        self, project_name: str, workspace_url: str, notes: str = ""
    ) -> str:
        engagement_id = f"eng_{uuid.uuid4().hex[:12]}"
        self._conn.execute(
            "INSERT INTO engagements (id, project_name, workspace_url, status, created_at, notes)"
            " VALUES (?, ?, ?, 'created', ?, ?)",
            (engagement_id, project_name, workspace_url, _now(), notes),
        )
        self._conn.commit()
        return engagement_id

    def get_engagement(self, engagement_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM engagements WHERE id = ?", (engagement_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_engagements(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM engagements ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def update_engagement(self, engagement_id: str, **fields: Any) -> None:
        if not fields:
            return
        allowed = {
            "workspace_id",
            "workspace_name",
            "status",
            "discovered_at",
            "scope_confirmed_at",
            "notes",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        assignments = ", ".join(f"{k} = ?" for k in updates)
        self._conn.execute(
            f"UPDATE engagements SET {assignments} WHERE id = ?",
            (*updates.values(), engagement_id),
        )
        self._conn.commit()

    # -- inventory & scope ----------------------------------------------------

    def replace_inventory(self, engagement_id: str, artifacts: list[dict[str, Any]]) -> None:
        self._conn.execute("DELETE FROM inventory WHERE engagement_id = ?", (engagement_id,))
        self._conn.executemany(
            "INSERT INTO inventory (engagement_id, artifact_id, artifact_type, item_type, name,"
            " proposed, rationale) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    engagement_id,
                    a["artifact_id"],
                    a["artifact_type"],
                    a["item_type"],
                    a["name"],
                    1 if a["included"] else 0,
                    a.get("rationale", ""),
                )
                for a in artifacts
            ],
        )
        self._conn.commit()

    def get_inventory(self, engagement_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM inventory WHERE engagement_id = ? ORDER BY item_type, name",
            (engagement_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def set_confirmed_scope(
        self, engagement_id: str, artifact_ids: list[str], item_ids: list[str]
    ) -> None:
        self._conn.execute(
            "DELETE FROM scope_artifacts WHERE engagement_id = ?", (engagement_id,)
        )
        self._conn.execute("DELETE FROM scope_checks WHERE engagement_id = ?", (engagement_id,))
        self._conn.executemany(
            "INSERT OR IGNORE INTO scope_artifacts (engagement_id, artifact_id) VALUES (?, ?)",
            [(engagement_id, a) for a in artifact_ids],
        )
        self._conn.executemany(
            "INSERT OR IGNORE INTO scope_checks (engagement_id, item_id) VALUES (?, ?)",
            [(engagement_id, i) for i in item_ids],
        )
        self._conn.commit()

    def get_confirmed_scope(self, engagement_id: str) -> tuple[list[str], list[str]]:
        artifacts = [
            r["artifact_id"]
            for r in self._conn.execute(
                "SELECT artifact_id FROM scope_artifacts WHERE engagement_id = ?",
                (engagement_id,),
            ).fetchall()
        ]
        checks = [
            r["item_id"]
            for r in self._conn.execute(
                "SELECT item_id FROM scope_checks WHERE engagement_id = ?", (engagement_id,)
            ).fetchall()
        ]
        return artifacts, checks

    # -- runs & findings ------------------------------------------------------

    def start_run(self, engagement_id: str) -> str:
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        self._conn.execute(
            "INSERT INTO runs (id, engagement_id, started_at, status) VALUES (?, ?, ?, 'running')",
            (run_id, engagement_id, _now()),
        )
        self._conn.commit()
        return run_id

    def finish_run(self, run_id: str, summary: dict[str, Any], status: str = "complete") -> None:
        self._conn.execute(
            "UPDATE runs SET finished_at = ?, status = ?, summary_json = ? WHERE id = ?",
            (_now(), status, json.dumps(summary), run_id),
        )
        self._conn.commit()

    def save_results(self, run_id: str, engagement_id: str, results: list[dict[str, Any]]) -> None:
        self._conn.executemany(
            "INSERT OR REPLACE INTO findings (run_id, engagement_id, item_id, result_json)"
            " VALUES (?, ?, ?, ?)",
            [(run_id, engagement_id, r["item_id"], json.dumps(r)) for r in results],
        )
        self._conn.commit()

    def latest_run(self, engagement_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE engagement_id = ? ORDER BY started_at DESC LIMIT 1",
            (engagement_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_results(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT result_json FROM findings WHERE run_id = ?", (run_id,)
        ).fetchall()
        return [json.loads(r["result_json"]) for r in rows]

    # -- audit log ------------------------------------------------------------

    def append_audit(self, entry: dict[str, Any], engagement_id: str | None = None) -> None:
        """Append a hash-chained audit entry. Called by the guardrail on every call."""
        row = self._conn.execute("SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
        prev_hash = row["hash"] if row else GENESIS_HASH
        ts = _now()
        payload = json.dumps(entry, sort_keys=True)
        digest = hashlib.sha256(f"{prev_hash}|{ts}|{payload}".encode()).hexdigest()
        self._conn.execute(
            "INSERT INTO audit_log (engagement_id, ts, entry_json, prev_hash, hash)"
            " VALUES (?, ?, ?, ?, ?)",
            (engagement_id, ts, payload, prev_hash, digest),
        )
        self._conn.commit()

    def get_audit_log(self, engagement_id: str | None = None, limit: int = 200) -> list[dict]:
        if engagement_id:
            rows = self._conn.execute(
                "SELECT * FROM audit_log WHERE engagement_id = ? ORDER BY seq DESC LIMIT ?",
                (engagement_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM audit_log ORDER BY seq DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            {
                "seq": r["seq"],
                "ts": r["ts"],
                "hash": r["hash"][:16],
                **json.loads(r["entry_json"]),
            }
            for r in rows
        ]

    def verify_audit_chain(self, limit: int = 5000) -> tuple[bool, str]:
        """Re-derive every hash. Returns (intact, message)."""
        rows = self._conn.execute(
            "SELECT * FROM audit_log ORDER BY seq ASC LIMIT ?", (limit,)
        ).fetchall()
        prev = GENESIS_HASH
        for row in rows:
            expected = hashlib.sha256(
                f"{prev}|{row['ts']}|{row['entry_json']}".encode()
            ).hexdigest()
            if expected != row["hash"] or row["prev_hash"] != prev:
                return False, f"Audit chain broken at entry {row['seq']}."
            prev = row["hash"]
        return True, f"Audit chain intact across {len(rows)} entries."

    def close(self) -> None:
        self._conn.close()


_store: Store | None = None


def get_store(settings: Settings | None = None) -> Store:
    global _store
    if _store is None:
        cfg = settings or get_settings()
        _store = Store(cfg.db_path)
    return _store
