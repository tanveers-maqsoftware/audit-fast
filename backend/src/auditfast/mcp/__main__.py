"""Entry point: ``python -m auditfast.mcp`` or the ``auditfast-mcp`` console script."""

from __future__ import annotations

from auditfast.mcp.server import run


def main() -> None:
    run()


if __name__ == "__main__":
    main()
