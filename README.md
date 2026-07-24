# AuditFAST

AI-assisted, **read-only** auditing of Microsoft Fabric workspaces.

This repository currently contains the design docs and the first executable component:
**AuditFAST Core as an MCP server**. Point it at a workspace, it discovers what is
there, proposes what is worth auditing, waits for you to confirm, then scores the
workspace against the baseline checklist and writes a report.

## The flow

```
sign in  ->  start engagement  ->  discover  ->  [you confirm]  ->  run audit  ->  report
             workspace URL +      inventory +                      deterministic
             project name         proposal                         rule engine
```

Nothing is audited until you confirm the proposed scope, and nothing is ever written
to your tenant — see [Read-only guarantee](#read-only-guarantee).

## Quickstart

### 1. Register an Entra app (once per tenant)

Create an app registration with:

- **Authentication** → "Allow public client flows" = **Yes** (device-code flow)
- **API permissions** → Microsoft Fabric, *delegated*: `Workspace.Read.All`, `Item.Read.All`
- Optionally `Item.ReadWrite.All` — see [the definition-reads caveat](#the-definition-reads-caveat)

No client secret is needed and none is stored.

### 2. Install

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

### 3. Register the server with your MCP client

Claude Code:

```powershell
claude mcp add auditfast --env AUDITFAST_CLIENT_ID=<app-id> --env AUDITFAST_TENANT_ID=<tenant-id> -- <repo>\.venv\Scripts\python.exe -m auditfast_mcp
```

Or as JSON, for any MCP client:

```json
{
  "mcpServers": {
    "auditfast": {
      "command": "C:\\path\\to\\audit-fast\\.venv\\Scripts\\python.exe",
      "args": ["-m", "auditfast_mcp"],
      "env": {
        "AUDITFAST_CLIENT_ID": "<app-registration-client-id>",
        "AUDITFAST_TENANT_ID": "<tenant-guid-or-organizations>",
        "AUDITFAST_ENABLE_DEFINITION_READS": "false"
      }
    }
  }
}
```

See [.env.example](.env.example) for every setting.

### 4. Run an audit

Ask your MCP client, in plain language:

> Sign in to AuditFAST, then audit
> `https://app.fabric.microsoft.com/groups/<workspace-guid>/list`
> for project "Contoso Orders Migration".

It will walk the tool sequence, stop at the scope proposal for your confirmation, and
hand back a scored report.

## Tools

| Tool | Purpose |
|------|---------|
| `auditfast_sign_in` / `auditfast_sign_in_complete` | Device-code sign-in with delegated read-only scopes |
| `auditfast_start_engagement` | Open an engagement from a workspace URL/GUID/name + project name |
| `auditfast_discover_workspace` | Inventory the workspace; propose relevant artifacts and checks, each with a rationale |
| `auditfast_confirm_scope` | The human-in-the-loop gate. Accepts includes/excludes. Nothing runs before it |
| `auditfast_run_audit` | Run the confirmed checks; return scores, findings, and rollups |
| `auditfast_get_report` | Render the Markdown report |
| `auditfast_get_audit_log` | The hash-chained log of every Fabric call made |
| `auditfast_status` | Config and sign-in state — start here when something misbehaves |
| `auditfast_list_checks` | The check catalog with rules and scoring semantics |

## What it checks

20 deterministic checks across 6 of the baseline's 13 areas, keyed to the same item IDs
a manual engagement uses so scores stay comparable:

| Area | Checks |
|------|--------|
| 1 — Architecture | Workspace naming/organization |
| 2 — Integration | Pipeline naming, parameterization, annotations, retry policy and bounds, failure paths, failure notification |
| 3 — Processing | Notebook naming, parameterization, hardcoded secrets/paths, execution timeout |
| 6 — Security | Least privilege, group-based access, SPN for automation, guest access |
| 11 — DevOps | Git integration, deployment pipelines |
| 12 — Cost | Capacity assignment, orphaned items |

Scores use the baseline 0–3 rubric; coverage-based checks band a pass fraction onto
that scale (≥95% → 3, ≥80% → 2, ≥40% → 1, else 0). The overall score is renormalized
across **audited areas only** and is explicitly not comparable to a full deep-dive audit.

Checks that cannot be evaluated report `evidence_unavailable` rather than scoring 0 —
a gap in coverage is never silently laundered into a finding.

## Read-only guarantee

- Every outbound call is described as an inert value and handed to a single
  **guardrail**, the only code in the repo that touches the network. Callers never
  receive an HTTP client or a token.
- The guardrail exposes `validate`, `execute`, and `aclose`. **There is no write path
  in the codebase** — not disabled, absent.
- REST policy is a double allowlist: known host, then an explicitly permitted
  (method, path) shape. `PUT`/`PATCH`/`DELETE` are refused outright; `POST` is refused
  except for `.../getDefinition`, which is a read.
- Every call — validation and execution — is written to a hash-chained audit log.
  `auditfast_get_audit_log` re-derives the chain on read, so a deleted or edited entry
  is detectable.
- Delegated auth means the server can only see what the signed-in auditor can see.

The guardrail suite in [tests/test_guardrail.py](tests/test_guardrail.py) is a release
gate: if it fails, do not point the server at a client tenant.

### The definition-reads caveat

Fabric exposes item definitions only through `POST .../getDefinition`, and that API
requires the delegated scope `Item.ReadWrite.All` even though the operation only reads.
AuditFAST does not request it by default. With read-only scopes alone you get the
workspace, security, DevOps, and cost checks; the pipeline and notebook checks report
`evidence_unavailable`.

To include them, set `AUDITFAST_ENABLE_DEFINITION_READS=true` and grant the scope. The
guardrail still refuses every write verb and every POST that is not a `getDefinition`,
so the scope grants a capability the code has no path to exercise.

## Development

```powershell
.\.venv\Scripts\python.exe -m pytest -q     # 81 tests
.\.venv\Scripts\python.exe -m ruff check src tests
```

Tests never touch a real tenant: rules run against synthetic evidence bundles, and the
end-to-end flow runs against a mocked Fabric API.

## Where this fits

| Document | Role |
|----------|------|
| [docs/09-high-level-design.md](docs/09-high-level-design.md) | Module structure, ports, control flows |
| [docs/10-technical-architecture-document.md](docs/10-technical-architecture-document.md) | Infrastructure, security, deployment |
| [docs/11-baseline-checklist-catalog.md](docs/11-baseline-checklist-catalog.md) | Check-record schema and the baseline catalog |
| [docs/12-mcp-server-design.md](docs/12-mcp-server-design.md) | How this server realizes the HLD, and what it defers |

The MCP server is the **Core** slice: deterministic rules, no LLM in the scoring path.
The reasoning the HLD assigns to an `LLMAdapter` is currently supplied by the MCP
client's own model, which reads the structured results and talks the auditor through
them.

## License

TBD
