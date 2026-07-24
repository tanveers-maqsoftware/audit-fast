"""Turn whatever the auditor pasted into a workspace reference.

Accepts a portal URL, a bare GUID, or a workspace name — auditors paste all three.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

_GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

# app.fabric.microsoft.com/groups/<guid>/..., /workspaces/<guid>, ?experience=... etc.
_URL_HINTS = ("/groups/", "/workspaces/")


@dataclass(frozen=True)
class ParsedWorkspaceRef:
    """Either a resolved workspace id, or a name that still needs looking up."""

    workspace_id: str | None
    workspace_name: str | None
    raw: str

    @property
    def needs_lookup(self) -> bool:
        return self.workspace_id is None


def parse_workspace_url(value: str) -> ParsedWorkspaceRef:
    raw = (value or "").strip()
    if not raw:
        raise ValueError("A workspace URL, GUID, or name is required.")

    # Bare GUID.
    if _GUID.fullmatch(raw):
        return ParsedWorkspaceRef(workspace_id=raw.lower(), workspace_name=None, raw=raw)

    parsed = urlparse(raw)
    if parsed.scheme in ("http", "https"):
        path = parsed.path or ""
        if any(hint in path for hint in _URL_HINTS):
            # Take the GUID that follows the hint segment, not just any GUID in the URL —
            # portal links often carry an item GUID after the workspace one.
            for hint in _URL_HINTS:
                idx = path.find(hint)
                if idx == -1:
                    continue
                tail = path[idx + len(hint) :]
                match = _GUID.match(tail)
                if match:
                    return ParsedWorkspaceRef(
                        workspace_id=match.group(0).lower(), workspace_name=None, raw=raw
                    )
        match = _GUID.search(raw)
        if match:
            return ParsedWorkspaceRef(
                workspace_id=match.group(0).lower(), workspace_name=None, raw=raw
            )
        raise ValueError(
            f"Could not find a workspace GUID in {raw!r}. Open the workspace in the Fabric "
            "portal and copy the URL, which looks like "
            "https://app.fabric.microsoft.com/groups/<workspace-guid>/list"
        )

    # Anything else is treated as a workspace display name to resolve by listing.
    return ParsedWorkspaceRef(workspace_id=None, workspace_name=raw, raw=raw)
