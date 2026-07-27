# AuditFAST — Backend

The AuditFAST engine and its two front doors: a **REST API** (for the web frontend) and
an **MCP server** (for MCP clients). Both call the same service layer, so they can never
drift apart.

Read-only, always: every Fabric call is validated by a guardrail that exposes no write
path, and recorded in a hash-chained audit log.

## Layout

```
src/auditfast/
  config.py        settings (env-driven)
  guardrail/       the read-only choke point — the only code that hits Fabric
  auth/            delegated device-code sign-in (no secrets stored)
  fabric/          typed read-only Fabric REST client
  inspectors/      evidence collection, one inspector per artifact type
  catalog/         baseline check catalog (YAML) + schema
  rules/           deterministic rule engine
  scoring/         0–3 rubric + category/area/pillar rollups
  scope.py         scope proposal (relevance + rationale)
  report.py        Markdown report render
  db/              SQLAlchemy models, session, repositories
  services/        orchestration — the shared engine
  api/             FastAPI adapter (routers, schemas, deps)
  mcp/             MCP server adapter
tests/             pytest suite (unit + end-to-end over a mocked Fabric)
alembic/           database migrations
```

**Layering:** domain (pure) → persistence (`db`) → services (orchestration) → adapters
(`api`, `mcp`). Nothing above the persistence layer writes SQL; the adapters hold no
business logic.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Configure an Entra app registration (public client, delegated `Workspace.Read.All` +
`Item.Read.All`) and set `AUDITFAST_CLIENT_ID` / `AUDITFAST_TENANT_ID`. See
[.env.example](.env.example) for every setting.

## Run the API

```powershell
.\.venv\Scripts\python.exe -m auditfast.api
# http://127.0.0.1:8080  — interactive docs at /docs, schema at /openapi.json
```

The interactive Swagger UI at `/docs` lets you exercise every endpoint by hand — this is
the fastest way to **test the backend on its own**, no frontend required.

## Run the MCP server

```powershell
.\.venv\Scripts\python.exe -m auditfast.mcp
```

Register it with an MCP client pointing at `python -m auditfast.mcp` with the same env.

## Test

```powershell
.\.venv\Scripts\python.exe -m pytest -q          # full suite
.\.venv\Scripts\python.exe -m pytest tests/test_guardrail.py   # the read-only gate
.\.venv\Scripts\python.exe -m ruff check src tests
```

Tests never touch a real tenant: rules run against synthetic evidence, and the API/MCP
end-to-end tests run against a mocked Fabric (`respx`) on a temporary SQLite database.

## Database & migrations

Defaults to a SQLite file under the data dir; set `AUDITFAST_DATABASE_URL` to a Postgres
DSN for production. Schema is created from the ORM on startup for dev; production changes
go through Alembic:

```powershell
.\.venv\Scripts\alembic.exe revision --autogenerate -m "describe change"
.\.venv\Scripts\alembic.exe upgrade head
```

## The read-only guarantee

- `guardrail.Guardrail` exposes only `validate`, `execute`, `aclose` — **no write path
  exists in the code.** REST is a double allowlist (host, then method+path).
- Every call is appended to a hash-chained audit log; `verify_chain()` re-derives it.
- Delegated auth means the server only ever sees what the signed-in auditor can see.
- `tests/test_guardrail.py` is a release gate — if it fails, do not point at a client.

See [../docs/12-mcp-server-design.md](../docs/12-mcp-server-design.md) and
[../docs/13-implementation-log.md](../docs/13-implementation-log.md).
