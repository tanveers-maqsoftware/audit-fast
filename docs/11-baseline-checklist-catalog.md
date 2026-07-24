# AuditFAST — Baseline Checklist Catalog

---

## Document Control

| Field | Value |
|-------|-------|
| Document | **Baseline Checklist Catalog** (product data asset) |
| Product | AuditFAST — AI-Powered Microsoft Fabric Migration Auditor |
| Companion documents | `09-high-level-design.md` (HLD), `10-technical-architecture-document.md` (TAD) |
| Purpose | The versioned, machine-readable **baseline checklist** that the Checklist Engine, Tailoring Rules Engine, Scoring Engine, and Inspector Suite all consume. |
| Status | Draft — Area 1 fully worked as the canonical pattern; Areas 2–13 pending. |

> **What this is.** The filled-in `02-audit-checklist.md` in the audit pack is the *result of one engagement* (Medline). This catalog is the opposite: the **clean, engagement-independent definition** of every check, with no scores and no client evidence. It is `CHECKLIST_SNAPSHOT.baseline_version` in the HLD domain model (HLD §5). Each engagement pins a copy of this baseline at creation, then the Tailoring Engine marks items `in_scope` / sets `weight` from the Project Context Profile (PCP).

---

## How the catalog drives the system

```
baseline catalog (this file)
   │
   ├─▶ Checklist Engine .......... generates the 13-sheet Excel workbook (one sheet per Area)
   ├─▶ Tailoring Rules Engine .... uses `applicability` to set in_scope / weight from the PCP
   ├─▶ Inspector Suite ........... uses `artifact_types` + `inspector` + `protocol` to collect Evidence
   └─▶ Scoring Engine ............ uses `scoring_intent` (0–3 anchors) + `na_rule` to score deterministically
```

Every field below maps to a consumer. If a field has no consumer, it does not belong here.

---

## Record schema

Each check is one record. Fields:

| Field | Type | Consumed by | Meaning |
|-------|------|-------------|---------|
| `item_id` | string | all | Stable ID, e.g. `1.2.4`. Never renumbered; retired IDs are tombstoned, not reused. |
| `area` | int (1–13) | Checklist Engine | Area number → Excel sheet. |
| `category` | string | Checklist Engine | Sub-group within the area. |
| `pillar` | enum | Scoring Engine | Business-pillar rollup for the executive scorecard. **See "Pillars" note — values below are placeholders pending the product-spec six pillars.** |
| `title` | string | all | The check text shown to the auditor. |
| `artifact_types` | list | Tailoring, Inspector | Which Fabric artifacts this applies to. Drives applicability by inventory and routes to the right Inspector. |
| `inspector` | enum \| `manual` | Inspector Suite | Which Inspector collects evidence; `manual` = no API path, auditor must supply evidence. |
| `protocol` | enum | Guardrail | `rest` \| `sql` \| `xmla` \| `kql` \| `storage` \| `none`. The Guardrail validator that applies. |
| `evidence` | string | Inspector, LLMAdapter | What to fetch/inspect to prove the check (a hint, not a hardcoded query — the LLM proposes the read, the Guardrail validates it). |
| `scoring_intent` | map 0–3 | Scoring Engine, LLMAdapter | Anchors for each score. This is what keeps scoring reproducible across engagements. |
| `na_rule` | string | Scoring Engine | Condition under which the item is N/A (excluded from rollup). |
| `applicability` | string | Tailoring Rules Engine | PCP signal that includes/excludes or reweights the item (e.g. `compliance:HIPAA`, `component:Warehouse`). `always` = baseline-in-scope. |
| `default_weight` | enum | Scoring Engine | `low` \| `normal` \| `high`. Starting weight before PCP multipliers. |
| `severity_hint` | enum | Finding Synthesizer | Default finding severity when scored 0–1: `critical` \| `high` \| `medium` \| `low`. |

### Core (rule-engine) fields — added per `fabric-well-architected-auditor.md`

The MVP is deterministic (no AI). These fields make each check runnable as a pure function and drive the WAF scorecard. `scoring_intent` above is retained for the Phase-4 AI layer; in Core, `rule` + `coverage_semantics` produce the score instead.

| Field | Type | Consumed by | Meaning |
|-------|------|-------------|---------|
| `automation_tier` | enum | Rule Engine, Report | `mvp-auto` (REST `getDefinition` + parse, buildable now) \| `phase2` (needs SQL/XMLA/OneLake/Azure connector) \| `manual` (design intent / docs / runtime — Excel round-trip only). |
| `common` | bool | Rule Engine | `true` = baseline check that runs for every project regardless of source system; `false` = conditional on `applicability`. |
| `rule` | string | Rule Engine | The deterministic logic in one line (what the check function computes), e.g. "coverage = activities with `policy.retry>=1` / total activities". `manual` items have none. |
| `coverage_semantics` | enum | Scoring Engine | `coverage` (0–100% of objects passing → banded to 0–3) \| `binary` (pass=3 / fail=0). |
| `remediation` | string | Report Generator | Pre-written fix text keyed to `item_id` (no AI). Shown verbatim on any 0–1 finding. |

---

## Artifact types & Inspector registry

`artifact_types` and `inspector` are closed enums so the Tailoring Engine and Inspector Suite stay in sync. One Inspector per artifact type (HLD §6.2).

| `artifact_type` | `inspector` | Primary `protocol`(s) | Notes |
|-----------------|-------------|-----------------------|-------|
| `workspace` | `WorkspaceInspector` | `rest` | Roles, settings, item inventory |
| `pipeline` | `PipelineInspector` | `rest` | Data Factory pipeline JSON, runs |
| `notebook` | `NotebookInspector` | `rest` | Notebook definition/code cells |
| `lakehouse` | `LakehouseInspector` | `rest`, `sql` | Tables/Files structure, shortcuts |
| `delta_table` | `DeltaTableInspector` | `sql` | `DESCRIBE DETAIL/EXTENDED`, TBLPROPERTIES |
| `warehouse` | `WarehouseInspector` | `sql` | Schemas, external tables |
| `semantic_model` | `SemanticModelInspector` | `xmla` | Model metadata, RLS (**boundary — see CertyFAST note**) |
| `report` | `ReportInspector` | `rest` | Report metadata (**boundary — see CertyFAST note**) |
| `eventhouse` | `EventhouseInspector` | `kql` | Log schema, retention |
| `connection` | `ConnectionInspector` | `rest` | Connections, gateway, auth method |
| `capacity` | `CapacityInspector` | `rest` | SKU, CU metrics, throttling |
| `tenant` | `TenantInspector` | `rest` | Tenant admin settings |
| `git` | `GitInspector` | `rest` | Git integration, deployment pipelines |
| `onelake` | `OneLakeInspector` | `storage` | File hierarchy, sizes, tiers |
| *(none)* | `manual` | `none` | Evidence not API-discoverable; auditor supplies it |

> **CertyFAST boundary.** Per the working notes, deep certification of `semantic_model` and `report` artifacts is owned by **CertyFAST**, not AuditFAST. Checks on those types in this catalog are limited to **standard/structural** checks (existence, RLS presence, source binding). Anything deeper is tagged `handoff: certyfast` and excluded from the AuditFAST score. No such items appear in Area 1.

---

## Pillars (resolved — per `fabric-well-architected-auditor.md`, 2026-07-23)

AuditFAST Core rates **five Azure Well-Architected pillars**; **Area 1 (Architecture) is cross-cutting `Foundation`**, not a scored pillar — it informs every pillar. Area → pillar rollup:

| Pillar | Rolls up areas |
|--------|----------------|
| `Reliability` | 9 (Reliability & Resilience), 2.4 (Error Handling), 5 (Data Quality) |
| `Security` | 6 (Security & Access Control), 7 (Compliance) |
| `CostOptimization` | 12 (Cost & Capacity) |
| `OperationalExcellence` | 10 (Monitoring), 11 (DevOps), 8 (Governance), 13 (Documentation) |
| `PerformanceEfficiency` | 3 (Processing), 4 (Modeling & Storage) |
| `Foundation` *(cross-cutting)* | 1 (Architecture & Design) — informs all five |

> **Open decision:** the HLD refers to "six pillars." If the team decides Architecture is a **scored 6th pillar** rather than cross-cutting Foundation, only the rollup changes, not the records. Until resolved, Area 1 items carry `pillar: Foundation` and a `contributes_to` list.

> **Note on Area 1 records below:** they were authored before the pillar model was resolved and still show indicative per-item pillar values. On the next pass they become `pillar: Foundation` + `contributes_to: [...]`. No structural rework.

---

## Area 1 — Architecture & Design

> Area weight and per-item weights are set per engagement by the Tailoring/Scoring engines; `default_weight` here is the pre-PCP starting point.

### 1.1 Solution Architecture

```yaml
- item_id: "1.1.1"
  area: 1
  category: "Solution Architecture"
  pillar: OperationalExcellence
  title: "Clear separation of concerns across Fabric workspaces (dev/test/prod)"
  artifact_types: [workspace]
  inspector: WorkspaceInspector
  protocol: rest
  evidence: "Enumerate workspaces bound to the project; identify dev/test/prod separation and their capacity assignments."
  scoring_intent:
    0: "Single workspace for all stages, or no environment separation."
    1: "Some separation but stages share a workspace or capacity ambiguously."
    2: "Dev/test/prod separated into distinct workspaces."
    3: "Distinct workspaces with documented promotion boundaries and restricted prod access."
  na_rule: "Never N/A — every solution has an environment strategy to assess."
  applicability: always
  default_weight: normal
  severity_hint: high

- item_id: "1.1.2"
  area: 1
  category: "Solution Architecture"
  pillar: PerformanceEfficiency
  title: "Medallion architecture properly implemented (Pre-Bronze → Bronze → Silver → Gold)"
  artifact_types: [lakehouse, notebook, onelake]
  inspector: LakehouseInspector
  protocol: rest
  evidence: "Identify the layers present and where each transformation responsibility (raw capture, dedupe, conform, aggregate) actually runs. Cross-check 1.2.2/1.2.4/1.2.5."
  scoring_intent:
    0: "No recognizable layered architecture."
    1: "Layers exist but responsibilities are badly misaligned (e.g. business logic in raw)."
    2: "All layers exist; some responsibilities misplaced (e.g. dedupe in Bronze not Silver)."
    3: "Layers exist with each responsibility in its intended layer; Bronze immutable."
  na_rule: "N/A only if the solution deliberately does not use a medallion pattern (documented)."
  applicability: always
  default_weight: high
  severity_hint: high

- item_id: "1.1.3"
  area: 1
  category: "Solution Architecture"
  pillar: OperationalExcellence
  title: "Architecture diagram exists and reflects actual implementation"
  artifact_types: []
  inspector: manual
  protocol: none
  evidence: "Auditor confirms a current architecture diagram exists and matches the discovered inventory/lineage."
  scoring_intent:
    0: "No architecture diagram."
    1: "Diagram exists but materially out of date vs. implementation."
    2: "Diagram exists and broadly matches implementation."
    3: "Diagram current, versioned, and verifiably matches discovered lineage."
  na_rule: "Never N/A."
  applicability: always
  default_weight: low
  severity_hint: medium

- item_id: "1.1.4"
  area: 1
  category: "Solution Architecture"
  pillar: PerformanceEfficiency
  title: "Appropriate Fabric component selection per workload (Lakehouse vs Warehouse vs Eventhouse)"
  artifact_types: [workspace, lakehouse, warehouse, eventhouse]
  inspector: WorkspaceInspector
  protocol: rest
  evidence: "Inventory item types in use; assess whether each workload maps to the appropriate Fabric engine."
  scoring_intent:
    0: "Clear component misuse (e.g. Lakehouse used where Warehouse required and failing)."
    1: "Some questionable component choices without justification."
    2: "Reasonable component choices for the workloads."
    3: "Component choices documented and justified against workload characteristics."
  na_rule: "Never N/A."
  applicability: always
  default_weight: normal
  severity_hint: medium

- item_id: "1.1.5"
  area: 1
  category: "Solution Architecture"
  pillar: CostOptimization
  title: "Cross-region ADLS Gen2 usage justified with documented rationale for region mismatch"
  artifact_types: [connection, onelake, capacity]
  inspector: ConnectionInspector
  protocol: rest
  evidence: "Compare ADLS Gen2 region vs. Fabric capacity region; check for documented rationale, egress-cost and latency awareness."
  scoring_intent:
    0: "Cross-region with no awareness of cost/latency impact."
    1: "Cross-region acknowledged but no rationale or mitigation."
    2: "Cross-region with documented rationale; cost/latency not fully quantified."
    3: "Co-located, or cross-region with quantified, accepted, and documented trade-off."
  na_rule: "N/A if no external ADLS Gen2 is used (all storage in OneLake same region)."
  applicability: "component:ADLSGen2"
  default_weight: normal
  severity_hint: medium

- item_id: "1.1.6"
  area: 1
  category: "Solution Architecture"
  pillar: Governance
  title: "Single source of truth — no duplicate data stores serving the same purpose"
  artifact_types: [lakehouse, warehouse, onelake]
  inspector: LakehouseInspector
  protocol: rest
  evidence: "Look for duplicate tables/datasets serving the same purpose across lakehouses/workspaces; check shortcuts vs. copies."
  scoring_intent:
    0: "Multiple uncontrolled copies of the same data."
    1: "Some duplication without governance."
    2: "Minimal duplication, mostly justified."
    3: "Single source of truth; sharing via shortcuts, not copies."
  na_rule: "Never N/A."
  applicability: always
  default_weight: normal
  severity_hint: medium

- item_id: "1.1.7"
  area: 1
  category: "Solution Architecture"
  pillar: Governance
  title: "Workspace organization follows a logical boundary (by domain, team, or environment)"
  artifact_types: [workspace]
  inspector: WorkspaceInspector
  protocol: rest
  evidence: "Assess whether workspace boundaries follow a consistent, stated organizing principle."
  scoring_intent:
    0: "No discernible organizing principle."
    1: "Inconsistent organization."
    2: "Consistent organizing principle applied."
    3: "Documented workspace taxonomy consistently applied."
  na_rule: "Never N/A."
  applicability: always
  default_weight: low
  severity_hint: low
```

### 1.2 Data Architecture

```yaml
- item_id: "1.2.1"
  area: 1
  category: "Data Architecture"
  pillar: Governance
  title: "Data flow lineage is traceable end-to-end from source to Gold layer"
  artifact_types: [workspace, lakehouse, pipeline, notebook]
  inspector: WorkspaceInspector
  protocol: rest
  evidence: "Use Fabric lineage view; confirm each hop source→Gold is traceable within Fabric."
  scoring_intent:
    0: "Lineage not traceable."
    1: "Partial lineage with gaps."
    2: "Lineage traceable within Fabric."
    3: "Lineage traceable end-to-end including upstream of Fabric."
  na_rule: "Never N/A."
  applicability: always
  default_weight: normal
  severity_hint: medium

- item_id: "1.2.2"
  area: 1
  category: "Data Architecture"
  pillar: PerformanceEfficiency
  title: "Each medallion layer has clearly defined purpose and transformation responsibility"
  artifact_types: [notebook, lakehouse]
  inspector: NotebookInspector
  protocol: rest
  evidence: "Inspect where dedupe/cleanse/conform/aggregate run; confirm they match the intended layer."
  scoring_intent:
    0: "Layer responsibilities undefined/chaotic."
    1: "Responsibilities defined but frequently violated."
    2: "Mostly aligned; isolated misplacement (e.g. dedupe in Bronze)."
    3: "Each responsibility runs in its intended layer."
  na_rule: "N/A if no medallion pattern (documented)."
  applicability: always
  default_weight: normal
  severity_hint: medium

- item_id: "1.2.3"
  area: 1
  category: "Data Architecture"
  pillar: PerformanceEfficiency
  title: "Pre-Bronze layer strategy defined (raw landing vs. staging)"
  artifact_types: [onelake, lakehouse]
  inspector: OneLakeInspector
  protocol: storage
  evidence: "Determine whether a Pre-Bronze/landing zone exists and its role (transient staging vs. durable raw)."
  scoring_intent:
    0: "No landing strategy; sources written straight into processed layers."
    1: "Landing exists but role unclear."
    2: "Pre-Bronze role defined."
    3: "Pre-Bronze role defined and documented with retention/cleanup policy."
  na_rule: "N/A if the architecture intentionally has no Pre-Bronze layer."
  applicability: "layer:PreBronze"
  default_weight: low
  severity_hint: low

- item_id: "1.2.4"
  area: 1
  category: "Data Architecture"
  pillar: Governance
  title: "Bronze layer captures immutable raw data with audit metadata (ingestion timestamp, source, batch ID)"
  artifact_types: [delta_table, notebook]
  inspector: DeltaTableInspector
  protocol: sql
  evidence: "Check Bronze write pattern (append vs MERGE/overwrite) and presence of ingestion_ts/source/batch_id columns; inspect Delta history for overwrites."
  scoring_intent:
    0: "Bronze overwritten with no audit metadata."
    1: "Bronze mutated via MERGE (not immutable) though some metadata present."
    2: "Append-only Bronze with partial audit metadata."
    3: "Immutable append-only Bronze with full audit metadata."
  na_rule: "N/A if no Bronze layer."
  applicability: "layer:Bronze"
  default_weight: high
  severity_hint: high

- item_id: "1.2.5"
  area: 1
  category: "Data Architecture"
  pillar: PerformanceEfficiency
  title: "Silver layer applies cleansing, deduplication, conforming, and type standardization"
  artifact_types: [notebook, delta_table]
  inspector: NotebookInspector
  protocol: rest
  evidence: "Confirm cleansing/dedupe/conform/type-cast logic executes in Silver notebooks (not pushed up to Bronze or down to Gold)."
  scoring_intent:
    0: "Silver performs none of these responsibilities."
    1: "Some responsibilities present; key ones (e.g. dedupe) misplaced to another layer."
    2: "Most responsibilities in Silver; minor gaps."
    3: "All cleansing/dedupe/conform/type-standardization in Silver."
  na_rule: "N/A if no Silver layer."
  applicability: "layer:Silver"
  default_weight: normal
  severity_hint: medium

- item_id: "1.2.6"
  area: 1
  category: "Data Architecture"
  pillar: PerformanceEfficiency
  title: "Gold layer is consumption-ready, aggregated, and modeled for analytics use cases"
  artifact_types: [delta_table, notebook]
  inspector: DeltaTableInspector
  protocol: sql
  evidence: "Inspect Gold tables for consumption-ready modeling (aggregates, conformed grain) aligned to analytics use cases."
  scoring_intent:
    0: "No distinct consumption-ready layer."
    1: "Gold exists but not modeled for consumption."
    2: "Gold modeled for analytics with minor gaps."
    3: "Gold fully consumption-ready and modeled to use cases."
  na_rule: "N/A if no Gold layer."
  applicability: "layer:Gold"
  default_weight: normal
  severity_hint: medium

- item_id: "1.2.7"
  area: 1
  category: "Data Architecture"
  pillar: Security
  title: "Shortcuts used appropriately — not bypassing governance or security boundaries"
  artifact_types: [lakehouse, onelake]
  inspector: LakehouseInspector
  protocol: rest
  evidence: "Enumerate shortcuts; check direction and whether any cross a security boundary or bypass RLS/OLS."
  scoring_intent:
    0: "Shortcuts bypass governance/security boundaries."
    1: "Shortcuts present with some boundary concerns."
    2: "Shortcuts used appropriately."
    3: "Shortcuts governed with documented review of boundary/security impact."
  na_rule: "N/A if no shortcuts are used."
  applicability: "feature:Shortcuts"
  default_weight: normal
  severity_hint: medium
```

### 1.3 Integration Architecture

```yaml
- item_id: "1.3.1"
  area: 1
  category: "Integration Architecture"
  pillar: Reliability
  title: "On-Premises Data Gateway properly configured and sized for SQL Server / Oracle loads"
  artifact_types: [connection]
  inspector: ConnectionInspector
  protocol: rest
  evidence: "Inspect gateway configuration, clustering/failover, and sizing relative to on-prem source load."
  scoring_intent:
    0: "Gateway misconfigured or a single unmonitored point of failure."
    1: "Gateway works but under-sized or no failover."
    2: "Gateway correctly configured and sized."
    3: "Clustered/failover gateway, sized and monitored."
  na_rule: "N/A if no on-prem sources (SQL Server/Oracle) are in scope."
  applicability: "source:OnPremSQL|source:Oracle"
  default_weight: normal
  severity_hint: medium

- item_id: "1.3.2"
  area: 1
  category: "Integration Architecture"
  pillar: Reliability
  title: "SAP Datasphere integration uses supported, stable connector (not custom workaround)"
  artifact_types: [connection, pipeline]
  inspector: ConnectionInspector
  protocol: rest
  evidence: "Identify the SAP Datasphere ingestion mechanism; confirm it is a supported connector, not a bespoke workaround."
  scoring_intent:
    0: "Unsupported/custom fragile integration."
    1: "Workaround with known fragility."
    2: "Supported connector in use."
    3: "Supported connector with documented, resilient configuration."
  na_rule: "N/A if SAP Datasphere is not a source."
  applicability: "source:SAPDatasphere"
  default_weight: normal
  severity_hint: medium

- item_id: "1.3.3"
  area: 1
  category: "Integration Architecture"
  pillar: Reliability
  title: "API ingestion has proper authentication, pagination, throttling, and error handling"
  artifact_types: [pipeline, notebook]
  inspector: PipelineInspector
  protocol: rest
  evidence: "Inspect API-source pipelines/notebooks for auth, pagination, throttling/backoff, and error handling."
  scoring_intent:
    0: "API ingestion lacks auth or error handling."
    1: "Basic ingestion; missing pagination/throttling/robust errors."
    2: "Auth, pagination, throttling, error handling present."
    3: "All present plus documented resilience (backoff, idempotency)."
  na_rule: "N/A if no API sources are in scope."
  applicability: "source:API"
  default_weight: normal
  severity_hint: medium

- item_id: "1.3.4"
  area: 1
  category: "Integration Architecture"
  pillar: Reliability
  title: "SharePoint Excel ingestion handles schema drift and file-not-found scenarios"
  artifact_types: [pipeline, notebook]
  inspector: PipelineInspector
  protocol: rest
  evidence: "Inspect SharePoint/Excel ingestion for schema-drift handling and missing-file handling/alerting."
  scoring_intent:
    0: "No handling; breaks silently on drift or missing file."
    1: "Partial handling; e.g. file-not-found handled but drift not."
    2: "Handles drift and missing files; alerting unconfirmed."
    3: "Handles both with confirmed alerting on mismatch."
  na_rule: "N/A if no SharePoint/Excel sources are in scope."
  applicability: "source:SharePointExcel"
  default_weight: low
  severity_hint: medium

- item_id: "1.3.5"
  area: 1
  category: "Integration Architecture"
  pillar: Security
  title: "Azure File Share integration uses secure access (Managed Identity vs SAS vs Key)"
  artifact_types: [connection]
  inspector: ConnectionInspector
  protocol: rest
  evidence: "Determine the auth method for Azure File Share access; prefer Managed Identity over SAS/account key."
  scoring_intent:
    0: "Account key or hardcoded credential."
    1: "SAS token in use."
    2: "Managed Identity but broad scope."
    3: "Managed Identity, least-privilege, documented."
  na_rule: "N/A if no Azure File Share source is in scope."
  applicability: "source:AzureFileShare"
  default_weight: normal
  severity_hint: medium

- item_id: "1.3.6"
  area: 1
  category: "Integration Architecture"
  pillar: Governance
  title: "All source connections inventoried (even if only in pipeline metadata)"
  artifact_types: [connection, pipeline]
  inspector: ConnectionInspector
  protocol: rest
  evidence: "Enumerate all source connections; check whether a consolidated inventory exists beyond scattered pipeline metadata."
  scoring_intent:
    0: "No inventory; connections undiscoverable."
    1: "Connections exist only implicitly in pipelines."
    2: "Connections captured in pipeline metadata only, no consolidated inventory."
    3: "Formal, consolidated, maintained source inventory."
  na_rule: "Never N/A."
  applicability: always
  default_weight: low
  severity_hint: low

- item_id: "1.3.7"
  area: 1
  category: "Integration Architecture"
  pillar: Security
  title: "Connection credentials use secure storage (Key Vault / Fabric-managed, not hardcoded)"
  artifact_types: [connection]
  inspector: ConnectionInspector
  protocol: rest
  evidence: "Determine credential storage for connections: Key Vault / Workspace Identity / Managed Identity vs personal accounts vs hardcoded. Cross-check Area 6 (6.1.3, 6.4.1, 6.4.2, 6.4.5)."
  scoring_intent:
    0: "Secrets hardcoded in code/config."
    1: "Not hardcoded, but personal accounts used; no Key Vault / Managed Identity."
    2: "Key Vault or managed identity used with minor gaps."
    3: "All credentials via Key Vault / Workspace Identity, least-privilege."
  na_rule: "Never N/A."
  applicability: always
  default_weight: high
  severity_hint: high
```

---

## Cross-item consistency (feeds the guardrail verifier)

Area 1 items that must stay consistent with items elsewhere — the Scoring Engine's verify pass (the "over-scoring" check from `review-findings.md`) enforces these:

| Item | Must reconcile with | Rule |
|------|--------------------|------|
| `1.1.2` | `1.2.2`, `1.2.4`, `1.2.5` | Cannot be `3` if any layer-responsibility item is `≤2`. |
| `1.3.7` | `6.1.3`, `6.4.1`, `6.4.2`, `6.4.5` | Cannot exceed the credential-security posture in Area 6. |
| `1.2.4` | `4.2.5`, `5.2.9` | If Bronze is mutable (MERGE), audit-metadata items downstream cannot claim full preservation. |

---

## Completion tracker

| Area | Items | Status |
|------|-------|--------|
| 1. Architecture & Design | 21 | ✅ Worked (this file) |
| 2. Data Integration & Ingestion | 36 | ⬜ Pending |
| 3. Data Processing & Transformation | 36 | ⬜ Pending |
| 4. Data Modeling & Storage | 30 | ⬜ Pending |
| 5. Data Quality Framework | 47 | ⬜ Pending |
| 6. Security & Access Control | 24 | ⬜ Pending |
| 7. Compliance & Regulatory | 31 | ⬜ Pending |
| 8. Data Governance | 12 | ⬜ Pending |
| 9. Reliability & Resilience | 19 | ⬜ Pending |
| 10. Monitoring & Observability | 17 | ⬜ Pending |
| 11. DevOps & Deployment | 22 | ⬜ Pending |
| 12. Cost Management & Capacity | 16 | ⬜ Pending |
| 13. Documentation & Knowledge Mgmt | 16 | ⬜ Pending |
| **Total** | **327** | **21 / 327** |
