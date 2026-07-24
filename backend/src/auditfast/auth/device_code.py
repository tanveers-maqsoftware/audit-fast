"""OAuth2 device-code flow against Microsoft Entra.

Delegated means the server acts as the signed-in auditor and can therefore only ever
see what that person could already see in the Fabric portal — least privilege comes
free (fabric-well-architected-auditor.md 5.1). No client secret is held anywhere.

The flow is split across two MCP tool calls because the auditor has to go and sign in
between them: ``begin`` returns the code, ``complete`` polls for the token.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import anyio
import msal

from auditfast_mcp.config import Settings, get_settings


class AuthError(RuntimeError):
    """Sign-in could not be completed."""


@dataclass
class PendingFlow:
    flow: dict[str, Any]
    started_at: float

    @property
    def expired(self) -> bool:
        return time.time() > self.flow.get("expires_at", self.started_at + 900)


class DeviceCodeAuthenticator:
    """Owns the MSAL app, the token cache, and the in-flight device code."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._pending: PendingFlow | None = None
        self._cache = msal.SerializableTokenCache()
        self._load_cache()
        self._app: msal.PublicClientApplication | None = None

    # -- cache ----------------------------------------------------------------

    def _load_cache(self) -> None:
        path = self._settings.token_cache_path
        if path.exists():
            try:
                self._cache.deserialize(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                # A corrupt cache is not fatal — the auditor just signs in again.
                pass

    def _save_cache(self) -> None:
        if not self._cache.has_state_changed:
            return
        path = self._settings.token_cache_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._cache.serialize(), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            # Windows ACLs do not map onto POSIX modes; the file still sits in the
            # user-scoped data dir.
            pass

    # -- app ------------------------------------------------------------------

    @property
    def app(self) -> msal.PublicClientApplication:
        if not self._settings.client_id:
            raise AuthError(
                "AUDITFAST_CLIENT_ID is not set. Register a public-client Entra app with "
                "the delegated scopes Workspace.Read.All and Item.Read.All, enable "
                "'Allow public client flows', and set AUDITFAST_CLIENT_ID (and "
                "AUDITFAST_TENANT_ID) in the MCP server environment."
            )
        if self._app is None:
            self._app = msal.PublicClientApplication(
                client_id=self._settings.client_id,
                authority=self._settings.authority,
                token_cache=self._cache,
            )
        return self._app

    # -- flow -----------------------------------------------------------------

    def signed_in_account(self) -> str | None:
        accounts = self.app.get_accounts()
        return accounts[0].get("username") if accounts else None

    async def begin(self) -> dict[str, Any]:
        """Start a device-code flow and return the code the auditor must enter."""

        def _initiate() -> dict[str, Any]:
            return self.app.initiate_device_flow(scopes=self._settings.scopes)

        flow = await anyio.to_thread.run_sync(_initiate)
        if "user_code" not in flow:
            raise AuthError(
                "Entra refused to start the device-code flow: "
                f"{flow.get('error_description', flow)}"
            )
        self._pending = PendingFlow(flow=flow, started_at=time.time())
        return {
            "user_code": flow["user_code"],
            "verification_uri": flow.get("verification_uri"),
            "expires_in_seconds": int(flow.get("expires_in", 900)),
            "message": flow.get("message"),
            "scopes_requested": self._settings.scopes,
        }

    async def complete(self, poll_seconds: int = 90) -> dict[str, Any]:
        """Poll for the token. Safe to call repeatedly until it succeeds or expires."""
        pending = self._pending
        if pending is None:
            raise AuthError("No sign-in is in progress — call auditfast_sign_in first.")
        if pending.expired:
            self._pending = None
            raise AuthError("The device code expired. Call auditfast_sign_in to get a new one.")

        deadline = time.time() + poll_seconds

        def _acquire() -> dict[str, Any]:
            return self.app.acquire_token_by_device_flow(
                pending.flow, exit_condition=lambda _flow: time.time() > deadline
            )

        result = await anyio.to_thread.run_sync(_acquire)
        self._save_cache()

        if "access_token" in result:
            self._pending = None
            claims = result.get("id_token_claims") or {}
            return {
                "signed_in": True,
                "account": claims.get("preferred_username") or self.signed_in_account(),
                "tenant_id": claims.get("tid"),
                "scopes_granted": result.get("scope", "").split(),
                "expires_in_seconds": result.get("expires_in"),
            }

        error = result.get("error")
        if error in {"authorization_pending", "slow_down", None}:
            return {
                "signed_in": False,
                "status": "pending",
                "hint": (
                    f"Still waiting for sign-in. Enter code {pending.flow['user_code']} at "
                    f"{pending.flow.get('verification_uri')}, then call "
                    "auditfast_sign_in_complete again."
                ),
            }
        self._pending = None
        raise AuthError(f"Sign-in failed: {result.get('error_description', error)}")

    async def get_token(self) -> str:
        """Return a valid access token, refreshing silently when possible."""
        accounts = self.app.get_accounts()
        if not accounts:
            raise AuthError("Not signed in. Call auditfast_sign_in first.")

        def _silent() -> dict[str, Any] | None:
            return self.app.acquire_token_silent(self._settings.scopes, account=accounts[0])

        result = await anyio.to_thread.run_sync(_silent)
        self._save_cache()
        if not result or "access_token" not in result:
            raise AuthError(
                "The cached credential expired and could not be refreshed. "
                "Call auditfast_sign_in to sign in again."
            )
        return result["access_token"]

    def sign_out(self) -> None:
        for account in self.app.get_accounts():
            self.app.remove_account(account)
        self._pending = None
        self._save_cache()


_authenticator: DeviceCodeAuthenticator | None = None


def get_authenticator() -> DeviceCodeAuthenticator:
    global _authenticator
    if _authenticator is None:
        _authenticator = DeviceCodeAuthenticator()
    return _authenticator
