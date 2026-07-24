# AuditFAST — MCP Server Design

---

## Document Control

| Field | Value |
|-------|-------|
| Document | **MCP Server Design** |
| Product | AuditFAST Core — Fabric Well-Architected Auditor |
| Companion documents | `09-high-level-design.md` (HLD), `10-technical-architecture-document.md` (TAD), `11-baseline-checklist-catalog.md`, `../Local/fabric-well-architected-auditor.md` |
| Audience | Engineers extending the server; reviewers assessing the read-only claim |
| Scope | The MCP delivery surface: tool contract, module realization, deviations from the HLD, and what is deferred. **Not** a restatement of the HLD's module design. |
| Status | Implemented — first milestone |

> **Positioning.** The HLD assumes a FastAPI + Celery web application with a React UI.
> This document describes the first *shipped* interface, which is an **MCP server**
> instead. The domain modules are the same ones the HLD specifies; only the front door
> and the orchestration model differ, and this document records exactly where.

---

## 1. Why MCP first

| Driver | Consequence |
|--------|-------------|
| The auditor already works in an MCP client (Claude Code, Copilot, IDE) | No UI to build before the engine is provable |
| The client supplies its own model | The HLD's `LLMAdapter` is unnecessary for Core — the client's model reads structured results and narrates them |
| Human-in-the-loop is native to a chat client | HLD gate 1 (scope confirmation) becomes a natural conversational turn rather than a screen |
| Tools return structured data | The rule engine stays deterministic; nothing in the scoring path is model-generated |

The trade is that long-running orchestration (HLD 7.2's resumable job graph) is out of
scope: an MCP tool call is request/response. See section 6.

---

## 2. Tool contract

Tools are the API. Each returns a dict; failures return `{"error", "next_step"}` so the
calling model can recover in-conversation rather than dead-ending.

| Tool | Reads Fabric | State transition |
|------|--------------|------------------|
| `auditfast_sign_in` / `_complete` | no | — (token cached) |
| `auditfast_start_engagement` | no | `-> created` |
| `auditfast_discover_workspace` | yes (inventory) | `created -> discovered` |
| `auditfast_confirm_scope` | no | `discovered -> scoped` |
| `auditfast_run_audit` | yes (evidence) | `scoped -> audited` |
| `auditfast_get_report` | no | — |
| `auditfast_get_audit_log` | no | — |
| `auditfast_status`, `_list_checks`, `_list_engagements` | no | — |

**The gate is enforced in code, not convention.** `auditfast_run_audit` refuses to run
unless `scope_confirmed_at` is set on the engagement, and `auditfast_confirm_scope`
requires an explicit `confirm=True` (its default is a dry run).

---

## 3. Module realization

Every HLD module that Core needs exists, under `src/auditfast_mcp/`:

| HLD module | Here | Note |
|------------|------|------|
| Guardrail (shared) | `guardrail/` | REST validator only; SQL/XMLA/KQL/storage validators are Phase 2 |
| Connection Manager (shared) | `auth/device_code.py` | Delegated device code; no secrets stored, MSAL cache only |
| Workspace Profiler (Scenario B) | `inspectors/` + `scope.py` | Discovery reuses the same inspectors as the audit, at lower depth — as the HLD requires |
| Inspector Suite | `inspectors/` | `WorkspaceInspector`, `PipelineInspector`, `NotebookInspector` |
| Tailoring Rules Engine | `scope.py` | Deterministic: applicability by artifact presence, with a cited rationale per decision |
| Checklist Engine | `catalog/` | YAML catalog; the Excel round-trip is deferred |
| Audit Orchestrator | `server.py` | Collapsed into the tool sequence — see section 6 |
| Scoring Engine | `scoring/rubric.py` | Pure; baseline rubric arithmetic |
| Finding Synthesizer | `rules/engine.py` + `catalog` | Findings are catalog-authored remediation text keyed to `item_id`, **not** model output |
| Report Generator | `report.py` | Deterministic Markdown render |
| LLM Adapter | *(absent)* | Supplied by the MCP client's model, outside the scoring path |
| Domain Doc Ingestor (Scenario A) | *(deferred)* | Scenario B only for now |

### 3.1 Extension points

- **New artifact type** → add an `Inspector`, register it in `inspectors/registry.py`.
- **New check** → add a YAML record and a rule function in `rules/`, register it in
  `RULES`. A test asserts every catalog record has a registered rule, so a check can
  never silently never-run.
- **New protocol** → add a validator and extend the guardrail's dispatcher.

---

## 4. The read-only guarantee, as built

Two of the TAD's layers are realized here; the third is the deployer's job.

| Layer | Status |
|-------|--------|
| **Delegated identity** (the auditor's own token) | Built. The server sees only what the signed-in person sees |
| **Application guardrail** | Built. Double allowlist per call; no write path exists in the code |
| **IAM least privilege** | Deployer's responsibility — grant the app registration read-only scopes only |

Structural properties a reviewer can verify in one sitting:

1. `Guardrail.execute` is the only function in the package that issues an HTTP request.
2. `Guardrail`'s public surface is `validate`, `execute`, `aclose` — asserted by a test.
3. `POST` is rejected by policy except for paths matching `.../getDefinition`.
4. Every validate and execute is appended to a hash-chained log, re-verified on read.

### 4.1 Known tension: `Item.ReadWrite.All`

Fabric's `getDefinition` endpoints require a delegated scope named `...ReadWrite...`
even though the operation is a pure read. Options considered:

| Option | Verdict |
|--------|---------|
| Request it always | Rejected — contradicts "no write scopes are ever requested" |
| Never request it | Rejected — drops 9 of 20 checks, including all pipeline and notebook checks |
| **Opt-in, off by default** | **Chosen** — the auditor decides, and the guardrail still refuses every write verb, so the scope grants a capability the code cannot exercise |

When off, the affected checks report `evidence_unavailable` and the scope proposal
warns before any run. The gap is visible in the report's coverage section, never hidden.

---

## 5. Scoring decisions

- **Coverage bands** (`≥95% → 3, ≥80% → 2, ≥40% → 1, else 0`) are Core-specific: a rule
  engine measures "what fraction of objects pass" and must land on the same 0–3 scale a
  human auditor uses. Documented in `scoring/rubric.py`.
- **Renormalization.** Core covers 6 of 13 areas. Area weights are renormalized across
  audited areas so a partial scan is not diluted by the areas it never inspected — and
  every report states this explicitly.
- **Absent evidence is not a zero.** `evidence_unavailable` is excluded from the score
  denominator and listed under Coverage Gaps. Scoring 0 for "we could not look" would
  manufacture findings.
- **Least privilege is not a ratio.** An early implementation scored the fraction of
  non-elevated role assignments, which penalized a well-run workspace with one Admin
  *group*. It now flags what actually breaches least privilege: an elevated role held by
  a named individual, or Admin spread past two break-glass principals.

---

## 6. Deliberate deviations from the HLD

| HLD element | Status here | Rationale |
|-------------|-------------|-----------|
| Celery job graph, resumable runs (7.2) | Not built | An MCP call is request/response. Acceptable while a run is minutes; revisit when SQL/XMLA inspection makes runs long |
| PostgreSQL | SQLite | Same aggregate shape (`store/db.py`); ports without reshaping |
| React UI | MCP client | The client *is* the UI |
| Excel checklist round-trip | Deferred | Scope edits happen through `confirm_scope` arguments |
| Scenario A (domain docs) | Deferred | Scenario B (workspace inference) proves the loop first |
| Per-item LLM check proposal | Not applicable | Core is deterministic by design |

---

## 7. Roadmap

| Phase | Adds |
|-------|------|
| **Now** | 20 REST checks, 6 areas, discovery + confirm + run + report, guardrail suite |
| **Next** | Multi-workspace engagements (dev/test/prod as one project), Excel round-trip, remaining REST checks (sensitivity labels, tenant settings) |
| **Phase 2** | SQL endpoint (Delta/table checks, Areas 4–5), XMLA (semantic models, within the CertyFAST boundary), OneLake storage, Azure DevOps/GitHub for Area 11 branch policies |
| **Phase 3** | Scenario A doc ingestion, cross-run delta reporting, the FastAPI/UI surface for non-MCP users |

---

*Prepared by MAQ Software — Fabric Practice. Subordinate to the product spec for intent
and to the HLD/TAD for structure; authoritative for the MCP surface.*
