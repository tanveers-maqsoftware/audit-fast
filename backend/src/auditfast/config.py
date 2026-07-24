"""Runtime configuration, read from environment variables.

Every setting has a safe default except the Entra client id, which the auditor must
supply — AuditFAST deliberately ships with no embedded app registration so each
deployment consents to its own read-only scopes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Delegated, read-only scopes (fabric-well-architected-auditor.md 5.1).
READ_ONLY_SCOPES: tuple[str, ...] = (
    "https://api.fabric.microsoft.com/Workspace.Read.All",
    "https://api.fabric.microsoft.com/Item.Read.All",
)

# Fabric's *Get ...Definition* endpoints require a ReadWrite-named delegated scope even
# though the operation itself only reads. Requesting it is therefore opt-in: without it
# the definition-based checks report `evidence_unavailable` instead of being skipped.
# The guardrail still refuses every write verb and every non-getDefinition POST, so the
# scope grants a capability the code has no path to use.
DEFINITION_SCOPES: tuple[str, ...] = ("https://api.fabric.microsoft.com/Item.ReadWrite.All",)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _default_data_dir() -> Path:
    override = os.environ.get("AUDITFAST_DATA_DIR")
    if override:
        return Path(override).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "AuditFAST"
    return Path.home() / ".auditfast"


@dataclass(frozen=True)
class Settings:
    """Immutable process settings."""

    client_id: str = field(default_factory=lambda: os.environ.get("AUDITFAST_CLIENT_ID", ""))
    tenant_id: str = field(
        default_factory=lambda: os.environ.get("AUDITFAST_TENANT_ID", "organizations")
    )
    data_dir: Path = field(default_factory=_default_data_dir)

    # Resource limits — protect the client's Fabric capacity (TAD 7, "Client-system safety").
    request_timeout_seconds: int = field(
        default_factory=lambda: _env_int("AUDITFAST_REQUEST_TIMEOUT_SECONDS", 30)
    )
    max_concurrent_calls: int = field(
        default_factory=lambda: _env_int("AUDITFAST_MAX_CONCURRENT_CALLS", 4)
    )
    max_response_bytes: int = field(
        default_factory=lambda: _env_int("AUDITFAST_MAX_RESPONSE_BYTES", 8 * 1024 * 1024)
    )
    max_items_inspected: int = field(
        default_factory=lambda: _env_int("AUDITFAST_MAX_ITEMS_INSPECTED", 200)
    )

    # Opt-in for definition reads (see DEFINITION_SCOPES).
    enable_definition_reads: bool = field(
        default_factory=lambda: _env_bool("AUDITFAST_ENABLE_DEFINITION_READS", False)
    )

    # Naming conventions are engagement-specific; these are the defaults the rules use
    # until an auditor overrides them at confirm-scope time.
    workspace_name_pattern: str = field(
        default_factory=lambda: os.environ.get(
            "AUDITFAST_WORKSPACE_NAME_PATTERN", r"^[A-Za-z0-9]+([ _-][A-Za-z0-9]+)*$"
        )
    )
    pipeline_name_pattern: str = field(
        default_factory=lambda: os.environ.get(
            "AUDITFAST_PIPELINE_NAME_PATTERN", r"^(PL|Pipeline)[_-][A-Za-z0-9_-]+$"
        )
    )
    notebook_name_pattern: str = field(
        default_factory=lambda: os.environ.get(
            "AUDITFAST_NOTEBOOK_NAME_PATTERN", r"^(NB|Notebook)[_-][A-Za-z0-9_-]+$"
        )
    )

    @property
    def authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}"

    @property
    def scopes(self) -> list[str]:
        scopes = list(READ_ONLY_SCOPES)
        if self.enable_definition_reads:
            scopes.extend(DEFINITION_SCOPES)
        return scopes

    @property
    def db_path(self) -> Path:
        return self.data_dir / "auditfast.sqlite3"

    @property
    def token_cache_path(self) -> Path:
        return self.data_dir / "token_cache.bin"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)


_settings: Settings | None = None


def get_settings() -> Settings:
    """Process-wide settings singleton (re-read with :func:`reset_settings` in tests)."""
    global _settings
    if _settings is None:
        _settings = Settings()
        _settings.ensure_dirs()
    return _settings


def reset_settings() -> None:
    global _settings
    _settings = None
