"""Load and index the MVP check catalog."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from auditfast.catalog.models import CheckRecord

CATALOG_PATH = Path(__file__).parent / "data" / "mvp_checks.yaml"


@lru_cache(maxsize=1)
def load_catalog() -> tuple[CheckRecord, ...]:
    raw = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8")) or []
    records = tuple(CheckRecord.model_validate(entry) for entry in raw)

    seen: set[str] = set()
    for record in records:
        if record.item_id in seen:
            raise ValueError(f"Duplicate item_id in catalog: {record.item_id}")
        seen.add(record.item_id)
    return records


def get_check(item_id: str) -> CheckRecord | None:
    return next((c for c in load_catalog() if c.item_id == item_id), None)


def checks_for_artifact_types(present: set[str]) -> list[CheckRecord]:
    """Checks whose artifact types are represented in the discovered inventory.

    A check with no artifact types is workspace-level and always applies.
    """
    applicable: list[CheckRecord] = []
    for check in load_catalog():
        if not check.artifact_types or set(check.artifact_types) & present:
            applicable.append(check)
    return applicable
