# AuditFAST — Implementation Log

A running record of what was built and why, so a reader can follow the codebase's
evolution without archaeology through git. Newest phase first.

---

## Phase 3 — Monorepo restructure to industry-standard layout (in progress)

**Goal.** Move from a single `auditfast_mcp` package to a clean, layered monorepo that a
new engineer can read, test, and extend. Support storing **user-added checklist items**
as first-class data. Make the backend independently runnable/testable and the
frontend integration trivial via a typed OpenAPI contract.

**Decisions (confirmed with the product owner).**

| Fork | Choice | Why |
|------|--------|-----|
| Frontend | **React + TypeScript + Vite + Tailwind/shadcn** | Dominant 2026 SPA stack; matches the TAD; typed client from OpenAPI |
| Layout | **Monorepo: `backend/` + `frontend/`** | Each side runs and tests alone; integration is the API contract |
| Backend | **Layered core `auditfast` + `api` and `mcp` adapters** | Name no longer implies MCP-only; one engine, two front doors |
| Storage | **SQLAlchemy 2.0 + Alembic on SQLite, Postgres-swappable** | ORM + migrations; clean home for user-added checklists |

### Target structure

```
audit-fast/
├── backend/
│   ├── pyproject.toml            # the Python project (installable, testable alone)
│   ├── alembic.ini, alembic/     # versioned DB migrations
│   ├── src/auditfast/
│   │   ├── config.py             # settings (env-driven)
│   │   ├── guardrail/            # read-only choke point (unchanged behaviour)
│   │   ├── auth/                 # delegated device-code sign-in
│   │   ├── fabric/               # typed read-only Fabric REST client
│   │   ├── inspectors/           # evidence collection per artifact type
│   │   ├── catalog/              # baseline check catalog (YAML) + schema
│   │   ├── rules/                # deterministic rule engine
│   │   ├── scoring/              # 0–3 rubric + rollups
│   │   ├── scope.py, report.py   # scope proposal + report render
│   │   ├── db/                   # SQLAlchemy models, session, repositories
│   │   ├── services/             # orchestration (the shared engine)
│   │   ├── api/                  # FastAPI adapter (routers, schemas, deps)
│   │   └── mcp/                  # MCP server adapter
│   └── tests/
├── frontend/                     # React + TS + Vite + Tailwind
│   └── src/ ...
├── docs/                         # design docs + this log
└── README.md
```

### Layers, top to bottom

1. **Domain** (`guardrail`, `fabric`, `auth`, `inspectors`, `catalog`, `rules`,
   `scoring`, `scope`, `report`) — pure logic, no web/DB knowledge. Fully unit-testable.
2. **Persistence** (`db`) — SQLAlchemy models + repositories. The only code that talks
   to the database.
3. **Services** (`services`) — orchestration. Coordinates auth, guardrail, inspectors,
   rules, scoring, and repositories. Returns plain dicts; knows nothing of HTTP or MCP.
4. **Adapters** (`api`, `mcp`) — thin. Translate an incoming request (HTTP or MCP tool
   call) into a service call and back. No business logic.

### Change record (this phase)

- Renamed package `auditfast_mcp` → `auditfast`; moved everything under `backend/`.
- `store/db.py` (hand-rolled SQLite) → `db/` (SQLAlchemy 2.0 models + repositories +
  Alembic). The audit-log hash chain is preserved as a repository method.
- New `custom_checks` table + repository + endpoints: auditors can add their own
  checklist items (scored manually) alongside the baseline catalog.
- `webapi/` → `api/`, split into routers (`auth`, `engagements`, `catalog`, `health`),
  a `schemas.py` (Pydantic request/response models → OpenAPI), and `deps.py`.
- `server.py` → `mcp/server.py`; both adapters call the same `services`.
- New `frontend/` React app consuming the OpenAPI-typed client.

---

## Phase 2 — Web UI over the shared engine (superseded by Phase 3)

Extracted orchestration from the MCP tool bodies into `services.py` so a second front
door could reuse it, then added a FastAPI app and a single-page HTML/JS dashboard.
Phase 3 promotes this into a proper React frontend and a layered backend.

---

## Phase 1 — AuditFAST Core as an MCP server

The first executable component: a read-only Fabric auditor exposed as MCP tools.

- **Guardrail** — the single choke point for outbound Fabric calls, with no write path
  in the codebase. REST double-allowlist (host, then method+path). Hash-chained audit log.
- **Delegated device-code auth** — the server sees only what the signed-in auditor sees.
- **Discovery → confirm → audit → report** loop across 12 MCP tools, with a mandatory
  human-in-the-loop scope-confirmation gate.
- **20 deterministic checks** across 6 baseline areas, keyed to the baseline checklist's
  item IDs; coverage bands map a pass-fraction onto the 0–3 rubric.
- **Two bugs caught by the tool's own output** and fixed: the least-privilege rule
  penalised small well-run workspaces (ratio-based); the pillar scorecard rolled up by
  area, showing "Security 100%" above a critical security finding (now rolls up by each
  check's own pillar tag).

Test coverage at end of phase: 82 tests (guardrail suite, rule engine, scoring/rollup,
URL parsing, audit-chain tamper detection, end-to-end over a mocked Fabric API).
