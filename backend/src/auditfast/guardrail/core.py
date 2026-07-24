"""The guardrail itself — validate, then execute under limits, logging both.

There is deliberately no ``write`` method on this class. The write path does not
exist in the codebase, which is what makes the read-only claim reviewable in one
place (HLD 8, guardrail layer L6).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

import httpx

from auditfast_mcp.config import Settings, get_settings
from auditfast_mcp.guardrail.models import Decision, GuardDecision, RawResult, RestCall
from auditfast_mcp.guardrail.rest_validator import validate_rest

# Signature of the audit-log sink the guardrail writes to.
AuditSink = Callable[[dict], None]


class GuardrailRejection(RuntimeError):
    """A call was refused by policy. Never retried — it is a correctness signal."""

    def __init__(self, call: RestCall, decision: GuardDecision) -> None:
        super().__init__(
            f"Guardrail rejected {call.redacted()}: {decision.reason} [{decision.rule}]"
        )
        self.call = call
        self.decision = decision


class TransientExternalError(RuntimeError):
    """Fabric was reachable but unhappy in a way worth retrying."""


class EvidenceUnavailable(RuntimeError):
    """Fabric refused the read (403/404). Surfaced to the auditor, never silently skipped."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class Guardrail:
    """Single choke point for outbound Fabric traffic."""

    def __init__(
        self,
        token_provider: Callable[[], Awaitable[str]],
        settings: Settings | None = None,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self._token_provider = token_provider
        self._settings = settings or get_settings()
        self._audit_sink = audit_sink
        self._semaphore = asyncio.Semaphore(self._settings.max_concurrent_calls)
        self._http: httpx.AsyncClient | None = None

    def _client(self) -> httpx.AsyncClient:
        """One pooled client per guardrail instance.

        A run can issue hundreds of calls; a fresh client per call would mean a fresh
        TLS handshake per call against the client's capacity.
        """
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=self._settings.request_timeout_seconds,
                limits=httpx.Limits(
                    max_connections=self._settings.max_concurrent_calls,
                    max_keepalive_connections=self._settings.max_concurrent_calls,
                ),
            )
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # -- policy ---------------------------------------------------------------

    def validate(self, call: RestCall) -> GuardDecision:
        """Return the policy decision for a call. Performs no I/O, ever."""
        return validate_rest(call)

    # -- execution ------------------------------------------------------------

    async def execute(self, call: RestCall) -> RawResult:
        """Validate then execute. The only function here that touches the network."""
        decision = self.validate(call)
        self._log(call, decision, phase="validate")
        if decision.decision is Decision.REJECTED:
            raise GuardrailRejection(call, decision)

        async with self._semaphore:
            token = await self._token_provider()
            started = time.perf_counter()
            try:
                response = await self._client().request(
                    call.method.upper(),
                    call.url,
                    params=call.params or None,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                    },
                    # getDefinition takes no body; sending one is not supported here.
                    json={} if call.method.upper() == "POST" else None,
                )
            except httpx.TimeoutException as exc:
                self._log(call, decision, phase="execute", error=f"timeout: {exc}")
                raise TransientExternalError(f"{call.redacted()} timed out") from exc
            except httpx.HTTPError as exc:
                self._log(call, decision, phase="execute", error=str(exc))
                raise TransientExternalError(f"{call.redacted()} failed: {exc}") from exc

            elapsed_ms = int((time.perf_counter() - started) * 1000)
            body_bytes = response.content or b""

            self._log(
                call,
                decision,
                phase="execute",
                status_code=response.status_code,
                byte_count=len(body_bytes),
                elapsed_ms=elapsed_ms,
            )

            if len(body_bytes) > self._settings.max_response_bytes:
                raise EvidenceUnavailable(
                    f"{call.redacted()} returned {len(body_bytes)} bytes, over the "
                    f"{self._settings.max_response_bytes}-byte cap"
                )

            if response.status_code in (401, 403):
                raise EvidenceUnavailable(
                    f"{call.redacted()} denied ({response.status_code}) — the signed-in "
                    "account lacks read access to this resource",
                    response.status_code,
                )
            if response.status_code == 404:
                raise EvidenceUnavailable(
                    f"{call.redacted()} not found (404) — the resource does not exist "
                    "or is not exposed to this account",
                    response.status_code,
                )
            if response.status_code == 429 or response.status_code >= 500:
                raise TransientExternalError(
                    f"{call.redacted()} returned {response.status_code}"
                )
            if response.status_code >= 400:
                raise EvidenceUnavailable(
                    f"{call.redacted()} returned {response.status_code}: {response.text[:200]}",
                    response.status_code,
                )

            try:
                payload = response.json() if body_bytes else None
            except ValueError:
                payload = None

            return RawResult(
                status_code=response.status_code,
                body=payload,
                byte_count=len(body_bytes),
                elapsed_ms=elapsed_ms,
            )

    # -- logging --------------------------------------------------------------

    def _log(
        self,
        call: RestCall,
        decision: GuardDecision,
        *,
        phase: str,
        status_code: int | None = None,
        byte_count: int | None = None,
        elapsed_ms: int | None = None,
        error: str | None = None,
    ) -> None:
        if self._audit_sink is None:
            return
        self._audit_sink(
            {
                "phase": phase,
                "protocol": call.protocol.value,
                "method": call.method.upper(),
                "url": call.url,
                "purpose": call.purpose,
                "decision": decision.decision.value,
                "rule": decision.rule,
                "reason": decision.reason,
                "status_code": status_code,
                "byte_count": byte_count,
                "elapsed_ms": elapsed_ms,
                "error": error,
            }
        )
