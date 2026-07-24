"""Value types crossing the guardrail boundary.

Callers build an *inert description* of the call they want. They never receive a live
HTTP client or a token — only the guardrail can turn a description into traffic
(HLD 8: "Callers receive an OutboundCall description, never a live client").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Protocol(StrEnum):
    REST = "rest"


class Decision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class RestCall:
    """A described-but-not-executed Fabric REST request."""

    method: str
    url: str
    purpose: str
    params: dict[str, str] = field(default_factory=dict)
    protocol: Protocol = Protocol.REST

    def redacted(self) -> str:
        """Loggable one-liner. Fabric URLs carry GUIDs, never secrets."""
        return f"{self.method.upper()} {self.url}"


@dataclass(frozen=True)
class GuardDecision:
    decision: Decision
    reason: str
    rule: str

    @property
    def approved(self) -> bool:
        return self.decision is Decision.APPROVED


@dataclass(frozen=True)
class RawResult:
    status_code: int
    body: dict | list | None
    byte_count: int
    elapsed_ms: int
