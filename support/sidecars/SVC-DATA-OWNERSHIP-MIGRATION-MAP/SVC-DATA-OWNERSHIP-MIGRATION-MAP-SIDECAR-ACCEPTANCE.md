# SVC-DATA-OWNERSHIP-MIGRATION-MAP Acceptance Packet

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `SVC-DATA-OWNERSHIP-MIGRATION-MAP-SIDECAR-ACCEPTANCE`
**Helper parent:** `SVC-DATA-OWNERSHIP-MIGRATION-MAP` — Map JSONL service stores to Postgres ownership migration slices
**Parent owner:** `Claude`
**Parent reviewer:** `Codex2`
**Prepared by:** `Claude2`
**Date:** `2026-04-29`
**Packet status:** `review_approved → finalized 2026-04-29 by Claude2`
**Review disposition:** `approved by Claude 2026-04-29`

> Scope constraint: support artifact only. This packet does not edit canonical truth, runtime
> contracts, DATABASE ownership policy, or the parent implementation. It packages the current
> store inventory, dependency map, ownership conflict flags, and acceptance checklist for
> `SVC-DATA-OWNERSHIP-MIGRATION-MAP`.

---

## 1. Purpose

This sidecar reduces restart cost for `SVC-DATA-OWNERSHIP-MIGRATION-MAP` by doing three things:

1. inventory all JSONL/JSON file stores in the current compose baseline, code-backed
2. map each store to its service owner, target Postgres schema, and migration priority per `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md`
3. flag cross-service read boundary violations and write-owner conflicts that the parent task must resolve before downstream pilot tasks can start

This packet is intentionally narrower than implementation work. It is meant to help `Claude`
produce the migration map without needing to re-scan the codebase from scratch.

---

## 2. Parent Task Truth

From `ai-status.json`, the parent task is currently:

- owner: `Claude`
- reviewer: `Codex2`
- phase: `Production Readiness / Data Ownership`
- status: `todo`
- formal dependencies: none
- recorded acceptance:
  1. code-backed inventory lists every default compose JSONL store
  2. each store has owner service, target schema, and migration priority
  3. cross-service read and write rules match DATABASE ownership policy
  4. first pilot scope and rollback path are named
  5. no runtime behavior changes

The parent is the blocking input for all three Postgres pilot tasks:
- `SVC-CONSULTATION-POSTGRES-STORE-PILOT`
- `SVC-POSTGRES-SOURCE-SEARCH-STORE-PILOT`
- `SVC-POSTGRES-TRAINING-RESEARCH-STORE-PILOT`

---

## 3. Sidecar Scope Boundary

In scope for this sidecar:

- scan `docker-compose.yml` and service code to inventory all file-backed stores
- map each store to the `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` schema/owner table
- flag write-owner conflicts and cross-service boundary violations
- expand the parent acceptance criteria into a reviewer-friendly checklist
- identify the minimal first pilot scope and rollback path

Out of scope:

- editing `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` or any L1 policy file
- writing migration scripts or schema DDL
- modifying any service implementation
- finalizing `SVC-DATA-OWNERSHIP-MIGRATION-MAP` lifecycle on behalf of the parent owner

---

## 4. Code-Backed Store Inventory

This inventory is derived from `docker-compose.yml` env vars, `services/*/main.py`, and
`services/*/store.py`. All paths are as configured in the compose default unless noted.

### 4.1 source-ingest-svc

Service: `services/source_ingestion/main.py`

| Store file | Env var | Compose default path | Kind |
|---|---|---|---|
| `ingest_schedule.jsonl` | `SOURCE_INGEST_STORE_PATH` | `/data/source-ingest/ingest_schedule.jsonl` | schedule records |
| `connector_config.jsonl` | `SOURCE_INGEST_CONNECTOR_STORE_PATH` | `/data/source-ingest/connector_config.jsonl` | connector config |
| `source_evidence.jsonl` | `SOURCE_INGEST_EVIDENCE_STORE_PATH` | `/data/source-ingest/source_evidence.jsonl` | evidence objects |
| `source_ingest_dlq.jsonl` | `SOURCE_INGEST_DLQ_PATH` | `/data/source-ingest/source_ingest_dlq.jsonl` | dead-letter queue |
| `source_ingest_audit.jsonl` | `SOURCE_INGEST_AUDIT_PATH` | `/data/source-ingest/source_ingest_audit.jsonl` | audit trail |

Write owner: source-ingest-svc for all five stores.
Cross-service read flag: search-svc reads `source_evidence.jsonl` via shared volume (see §5.2).

### 4.2 search-svc

Service: `services/search/main.py`, `services/search/index_store.py`

| Store file | Env var | Compose default path | Kind |
|---|---|---|---|
| `search-index.jsonl` | `SEARCH_INDEX_STORE_PATH` | `/data/search/search-index.jsonl` | search index snapshots |
| `source_evidence.jsonl` (read-only) | `SEARCH_EVIDENCE_STORE_PATH` | `/data/source-ingest/source_evidence.jsonl` | cross-service read |

Write owner: search-svc for `search-index.jsonl`.
Cross-service read flag: search-svc maps directly to source-ingest volume path — this is a
volume-share boundary violation that the Postgres migration must resolve (§5.2).

### 4.3 consultation-svc

Service: `services/consultation/store.py`, `services/consultation/main.py`

| Store file | Default path | Kind |
|---|---|---|
| `consult_audit.jsonl` | `/data/consultation/consult_audit.jsonl` | audit trail |
| `consult_memo_publications.jsonl` | `/data/consultation/consult_memo_publications.jsonl` | memo publication events |
| `consult_lifecycle_events.jsonl` | `/data/consultation/consult_lifecycle_events.jsonl` | lifecycle events |
| `consult_outbox.jsonl` | `/data/consultation/consult_outbox.jsonl` | outbox records |

Write owner: consultation-svc for all four stores.
No cross-service write boundary violation found.

### 4.4 capital-svc

Service: `services/capital/main.py`

| Store file | Default path | Kind |
|---|---|---|
| `capital_pools.json` | `/data/capital/capital_pools.json` | pool state |
| `persona_capital_bindings.json` | `/data/capital/persona_capital_bindings.json` | binding state |
| `capital_audit.jsonl` | `/data/capital/capital_audit.jsonl` | audit trail |

Write owner: capital-svc for all three stores.
No cross-service write boundary violation found.

### 4.5 runtime-manager-svc

Service: `services/runtime-manager/main.py`

| Store file | Env var | Compose default path | Kind |
|---|---|---|---|
| `runtime_bindings.json` | `PANTHEON_RUNTIME_BINDING_STORE_PATH` | `/data/runtime/runtime_bindings.json` | runtime binding state |

Write owner: runtime-manager-svc.
No cross-service write boundary violation found.

### 4.6 governance-svc

Service: `services/governance/main.py`

| Store file | Default path | Kind |
|---|---|---|
| `approval_decisions.json` | `$GOVERNANCE_DATA_DIR/approval_decisions.json` | approval decisions |
| `audit.jsonl` | `$GOVERNANCE_DATA_DIR/audit.jsonl` | governance audit trail |

Write owner: governance-svc.
Note: promotion-svc has its own `approval_decisions.json` in a separate data dir — not the same
file (see §4.7). These must be clearly mapped to different Postgres schemas.

### 4.7 promotion-svc

Service: `services/promotion/main.py`

| Store file | Default path | Kind |
|---|---|---|
| `approval_decisions.json` | `$PROMOTION_DATA_DIR/approval_decisions.json` | promotion approvals |
| `deployment_plans.json` | `$PROMOTION_DATA_DIR/deployment_plans.json` | deployment plans |
| `deployment_plan_extensions.json` | `$PROMOTION_DATA_DIR/deployment_plan_extensions.json` | plan extensions |

Write owner: promotion-svc.
Note: same filename `approval_decisions.json` as governance-svc but in a different data dir.
The Postgres migration must assign these to separate schemas to avoid naming collision.

### 4.8 incidents-svc and postmortems-svc

Services: `services/incidents/main.py`, `services/postmortems/main.py`

| Store file | Env var | Default path | Kind |
|---|---|---|---|
| `incidents.json` | `INCIDENTS_DATA_DIR` | `/tmp/pantheon/incidents/incidents.json` | incidents + postmortems |

**Write-owner conflict (§5.1):** Both incidents-svc and postmortems-svc write to the same
`incidents.json` file. The `postmortems/main.py` comment says:

> Shared IncidentStore — postmortem service uses the same backing store so that referential
> integrity (postmortem references incident) is enforced in-process. In production, both
> services connect to the shared Pantheon incidents DB schema.

This is the only intentional multi-service shared write store in the current baseline. Per the
L1 database ownership policy, both services should migrate together under the
`incident / postmortem / evolution` → `telemetry-evolution-svc` schema unless the parent
explicitly reassigns the write owner.

### 4.9 lineage-read-svc

Service: `services/lineage-read/main.py`

| Store file | Env var | Default path | Kind |
|---|---|---|---|
| `lineage.json` | `LINEAGE_DATA_DIR` | `/tmp/pantheon/lineage/lineage.json` | lineage records |

Write owner: lineage-read-svc.
No cross-service write boundary violation found.

### 4.10 BFF (control-plane/bff)

Service: `services/control-plane/bff/main.py`

| Store file | Default path | Kind |
|---|---|---|
| `commands.jsonl` | `$BFF_DATA_DIR/commands.jsonl` | command queue |
| `read_surfaces.json` | `$BFF_DATA_DIR/read_surfaces.json` | BFF read surface snapshots |

Write owner: bff-svc for both stores.
No cross-service write boundary violation found.

### 4.11 trader-feedback-svc (control-plane/feedback)

Service: `services/control-plane/feedback/main.py`

| Store file | Env var | Compose default path | Kind |
|---|---|---|---|
| `trader_feedback_events.jsonl` | `TRADER_FEEDBACK_STORE_PATH` | `/data/feedback/trader_feedback_events.jsonl` | trader feedback events |

Write owner: trader-feedback-svc.
No cross-service write boundary violation found.

### 4.12 research-svc

Service: `services/research/store.py`

| Store file | Default path | Kind |
|---|---|---|
| `research_events.jsonl` | `/data/research/research_events.jsonl` | research run events |
| per-session JSON files | `services/research/ingest/store/<session_id>/<material_id>.json` | ingested research materials |

Write owner: research-svc for both stores.
Note: the per-session JSON store is under the services/ tree, not a data volume — may need
special handling in the migration path.

### 4.13 training-session-svc

Service: `services/training-session/store.py`

| Store file | Default path | Kind |
|---|---|---|
| `teaching_events.jsonl` | `/data/training-session/teaching_events.jsonl` | teaching/training events |

Write owner: training-session-svc.
No cross-service write boundary violation found.

### 4.14 research-worker-gateway-svc

Service: `services/research-worker-gateway/store.py`

| Store file | Default path | Kind |
|---|---|---|
| `worker_events.jsonl` | `/data/research-worker-gateway/worker_events.jsonl` | worker dispatch events |

Write owner: research-worker-gateway-svc.
No cross-service write boundary violation found.

### 4.15 memory-svc

Service: `services/memory/main.py`

| Store file | Env var | Default path | Kind |
|---|---|---|---|
| `(memory store)` | `PANTHEON_MEMORY_STORE` | `/tmp/pantheon/memory/memory.json` (inferred) | institutional memory |

Write owner: memory-svc.

---

## 5. Ownership Conflicts and Boundary Violations

### 5.1 Write-Owner Conflict: incidents.json

`incidents-svc` and `postmortems-svc` both instantiate `IncidentStore(path=STORE_PATH)` pointing
to the same file. Both services can write to it.

**Policy violation:** `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` §3.1 requires
single-table single-owner.

**Resolution path for the parent task:** The parent should explicitly name either:
- `incidents-svc` as the sole writer and require postmortems-svc to call incidents-svc API
- OR acknowledge that these two services co-locate into one service-owner unit for this schema

The existing code comment suggests the intent is to migrate them together under one
`incident/postmortem` schema — that decision should be made explicit in the migration map.

### 5.2 Cross-Service Volume Read: search-svc reads source-ingest volume

`search-svc` sets `SEARCH_EVIDENCE_STORE_PATH` to the same path as
`SOURCE_INGEST_EVIDENCE_STORE_PATH` (`/data/source-ingest/source_evidence.jsonl`). This is a
cross-service volume share, not an API call.

**Policy violation:** `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` §3.3 says non-owners
should call the owner service API when cross-domain access is needed.

**Resolution path for the parent task:** After migration to Postgres:
- `source_evidence` table is owned by `source-ingest-svc` (write role)
- search-svc reads via a `read role` on the same table, or via a source-ingest read API
- The shared volume path disappears in Postgres — the parent should name which read pattern
  is preferred for the pilot

### 5.3 Filename Collision: approval_decisions.json

Both `governance-svc` and `promotion-svc` use the filename `approval_decisions.json` in their
respective data dirs. In Postgres these must land in different schemas
(`governance.approval_decisions` vs `promotion.approval_decisions` or similar).

Not a write-ownership violation since the data dirs are separate, but it is a naming ambiguity
that the migration map should resolve explicitly.

---

## 6. Mapping to DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY

Cross-reference of current stores to the target ownership table in
`DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` §4:

| Current service | Store files (§4) | L1 target schema | L1 write owner |
|---|---|---|---|
| source-ingest-svc | 5 JSONL stores | `registry.source` (or dedicated `source_ingest` schema) | registry-core-svc or source-ingest-svc¹ |
| search-svc | `search-index.jsonl` | no named schema in L1 | search-svc (new schema needed) |
| consultation-svc | 4 JSONL stores | `consult` | consultation-svc |
| capital-svc | 2 JSON + 1 JSONL | `capital` | capital-pool-svc |
| runtime-manager-svc | `runtime_bindings.json` | `runtime` | runtime-manager-svc |
| governance-svc | `approval_decisions.json` + audit | `governance` | governance-svc / promotion-svc |
| promotion-svc | 3 JSON stores | `governance` | promotion-svc² |
| incidents-svc + postmortems-svc | `incidents.json` (shared) | `incident / postmortem` | telemetry-evolution-svc (L1) or incidents-svc (code) |
| lineage-read-svc | `lineage.json` | no named schema in L1 | lineage-read-svc (new schema needed) |
| bff-svc | `commands.jsonl` + `read_surfaces.json` | no named schema in L1 | bff-svc (new schema needed) |
| trader-feedback-svc | `trader_feedback_events.jsonl` | `telemetry` (or `feedback`) | telemetry-svc or trader-feedback-svc |
| research-svc | `research_events.jsonl` + per-session JSON | no named schema in L1 | research-svc (new schema needed) |
| training-session-svc | `teaching_events.jsonl` | no named schema in L1 | training-session-svc (new schema needed) |
| research-worker-gateway-svc | `worker_events.jsonl` | no named schema in L1 | research-worker-gateway-svc (new schema needed) |
| memory-svc | memory store | no named schema in L1 | memory-svc (new schema needed) |

¹ `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` maps `registry.source` to `registry-core-svc`.
  Whether source-ingest-svc becomes the writer or delegates to registry-core-svc is a decision
  the parent map must make explicit.

² `promotion-svc` is listed alongside `governance-svc` in the L1 ownership table. The parent
  map should clarify schema separation between governance approval decisions and promotion
  deployment plans.

---

## 7. Acceptance Checklist (Expanded)

This expands the five parent acceptance criteria into a reviewer-friendly checklist.

| Check | What "done" means for the parent | Gap assessment |
|---|---|---|
| AC-1: Code-backed JSONL inventory | Every JSONL/JSON file store listed in docker-compose.yml and service code is named, with path, env var, and owning service | Provided in §4 of this sidecar; parent should verify completeness |
| AC-2: Owner + schema + priority per store | Each row in the inventory has a named write owner, a target Postgres schema, and a migration priority tier | §6 provides the cross-reference; priority tiers TBD by parent |
| AC-3: Cross-service read/write rules match DATABASE policy | Boundary violations in §5 are resolved — write-owner for incidents/postmortems is named, search-to-source-ingest read path is named | Open: both conflicts in §5 need explicit owner decisions |
| AC-4: First pilot scope and rollback path are named | Parent names which store(s) are the pilot candidate and what the JSONL fallback / rollback path is | Open: downstream pilot tasks suggest consultation-svc, source-ingest/search, and training/research as candidates — parent must name a first one |
| AC-5: No runtime behavior changes | Map is pure documentation; no env var, route, or compose default is changed | Met by default for a mapping artifact |
| AC-6: Filename collisions resolved | `approval_decisions.json` naming collision between governance-svc and promotion-svc is resolved in the schema naming | Open: §5.3 flags the collision; parent must name the schema split |
| AC-7: Schemas not in L1 are named | search, lineage, bff, research, training, memory schemas are explicitly proposed even if not yet in `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` | Open: §6 marks these as needing new schemas |

### Acceptance summary

Current parent acceptance is **not yet met** — the parent task has not yet produced the
migration map artifact. This sidecar provides the inventory and gap analysis the parent needs
to produce that artifact.

What this sidecar has already resolved for the parent:

- code-backed store inventory (AC-1) — §4
- write-owner baseline for each store — §4 and §6
- cross-reference to L1 ownership targets — §6
- boundary violation list — §5

What still requires parent decisions:

- priority tier assignment for each store (AC-2)
- explicit resolution of write-owner conflict for incidents/postmortems (AC-3)
- explicit resolution of search-to-source-ingest read path post-migration (AC-3)
- naming of the first pilot scope and rollback path (AC-4)
- schema names for stores not already covered in L1 (AC-7)

---

## 8. Dependency Map

### 8.1 Formal task dependency truth

Per `ai-status.json`, `SVC-DATA-OWNERSHIP-MIGRATION-MAP` has no formal `depends_on` entries.

This sidecar does not invent a blocker in task state.

### 8.2 Canonical sources the parent should read

| Source | Relevance |
|---|---|
| `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` | L1 canonical ownership policy — §4 is the target schema/owner map |
| `docker-compose.yml` | Authoritative compose default paths for store env vars |
| `services/source_ingestion/main.py` | All five source-ingest store paths and env vars |
| `services/search/main.py` | Cross-service volume read from source-ingest |
| `services/consultation/store.py` | Four consultation JSONL stores |
| `services/incidents/main.py`, `services/postmortems/main.py` | Shared IncidentStore write conflict |
| `services/governance/main.py`, `services/promotion/main.py` | Separate data dirs; same filename collision |
| `services/capital/main.py` | Capital store paths |
| `services/runtime-manager/main.py` | Runtime binding store path and env var |
| `services/training-session/store.py`, `services/research/store.py`, `services/research-worker-gateway/store.py` | Event stores for training/research group |

### 8.3 Downstream tasks blocked on parent

| Task | Owner | Status | Why parent map is needed |
|---|---|---|---|
| `SVC-CONSULTATION-POSTGRES-STORE-PILOT` | Claude2 | `todo` (depends_on parent) | Needs migration slice naming table owner and read contract |
| `SVC-POSTGRES-SOURCE-SEARCH-STORE-PILOT` | Claude2 | `todo` (depends_on parent + 2 others) | Needs source-ingest write owner and search read path decision |
| `SVC-POSTGRES-TRAINING-RESEARCH-STORE-PILOT` | Codex2 | `todo` (depends_on parent) | Needs migration slice for training/research/policy-learning event stores |

All three pilots are blocked until the parent map names each store's target schema, write owner,
and first pilot candidate.

---

## 9. Suggested First Pilot Scope

Based on the inventory and boundary analysis, this sidecar suggests the parent consider
**consultation-svc** as the first pilot for the following reasons:

- consultation-svc has no write-owner conflict — it is the sole writer of all four JSONL stores
- no cross-service volume read dependency on consultation stores
- `SVC-CONSULTATION-POSTGRES-STORE-PILOT` is already assigned and waiting
- the pilot has a natural JSONL fallback path — consul stores can revert to file if the env
  flag is removed (per the existing pattern in similar pilots)

Rollback path for a consultation pilot:
- disable `PANTHEON_CONSULTATION_POSTGRES_ENABLED` (or equivalent env flag)
- restart consultation-svc
- JSONL stores on volume persist and remain consistent since the pilot writes both paths or
  uses JSONL as the sole write path while Postgres-backed path is tested

This is a suggested scope. The parent owner decides.

---

## 10. Reviewer Focus Areas

For the parent reviewer (Codex2) and for Claude as parent owner:

### 10.1 Do not let the map skip non-JSONL flat stores

The inventory includes `.json` stores (not strictly JSONL) such as `runtime_bindings.json`,
`capital_pools.json`, `incidents.json`, etc. These are also candidates for Postgres migration
and should be included in the parent map rather than excluded because they are not line-delimited.

### 10.2 The incidents/postmortems conflict must have an explicit owner decision

The parent acceptance criterion says "cross-service read and write rules match DATABASE ownership
policy." That policy requires single-owner. The shared write to `incidents.json` must be
explicitly resolved in the map — either by selecting one service as the authoritative writer or
by declaring the two services as a co-located owner unit.

### 10.3 Search-to-source-ingest boundary must be named before the search pilot can proceed

`SVC-POSTGRES-SOURCE-SEARCH-STORE-PILOT` specifically depends on this parent map to determine
whether search-svc will use a read role on the Postgres table or call source-ingest-svc via API.
That decision must be in the map, not deferred to the pilot.

### 10.4 L1 schema table has gaps; parent should name new schemas rather than leave them implicit

Five or more service stores have no L1-named target schema. If the parent map leaves these
without a schema name, the pilot tasks will have to invent schema names independently, risking
divergence. The map should propose schema names even if they are provisional.

### 10.5 Do not widen scope into actual migration DDL or schema bootstrap

The parent task acceptance explicitly says "no runtime behavior changes." The map artifact should
stop at naming owner, schema, and priority. DDL, migration scripts, and schema bootstrap belong
to the individual pilot tasks.

---

## 11. Verification Performed For This Sidecar

Evidence basis for the store inventory in §4:

```
grep -rn "STORE_PATH\|store_path\|\.jsonl\|JSONL" services/*/main.py services/*/store.py
grep -n "STORE_PATH\|JSONL\|jsonl" docker-compose.yml
```

Observed: 15 service store groups identified; 2 write-owner boundary violations flagged (§5.1,
§5.2); 1 naming collision flagged (§5.3).

Not claimed as part of this sidecar:

- running any service tests
- verifying Postgres connectivity
- modifying any service store code

---

## 12. Review Outcome and Finalization Note

**Reviewer:** Claude  
**Approved:** 2026-04-29T17:09:30Z  
**Review file:** `.orchestrator/chair-reviews/20260429-SVC-DATA-OWNERSHIP-MIGRATION-MAP-SIDECAR-ACCEPTANCE-claude-review.md`

Review confirmed:
- 15 service store groups in §4 are complete and code-backed.
- 2 write-boundary violations (§5.1, §5.2) correctly flagged with L1 policy citations.
- 1 naming collision (§5.3) correctly classified as schema ambiguity, not write-owner violation.
- L1 cross-reference in §6 is accurate; 7 stores without named L1 schemas are explicitly marked open.
- Gap assessment in §7 is honest — AC-2 through AC-4, AC-6, AC-7 correctly marked open.
- Scope boundary maintained — no canonical truth modified.

**Open decisions remaining for parent owner (Claude):**

1. Incident/postmortem write owner (incidents-svc sole writer vs co-located unit).
2. Search read path post-migration (read role vs source-ingest API).
3. Schema names for 7 stores not yet in L1 (search, lineage, bff, research, training-session, research-worker-gateway, memory).
4. First pilot scope confirmation (consultation-svc suggested in §9).
5. Priority tier assignment per store (AC-2).

These remain open in `SVC-DATA-OWNERSHIP-MIGRATION-MAP` and do not block this sidecar's closeout.

---

## 13. Handoff

Recommended reviewer: `Claude`
Parent owner to absorb or ignore: `Claude`

Suggested handoff summary:

> `SVC-DATA-OWNERSHIP-MIGRATION-MAP` has a full code-backed store inventory in this sidecar
> (15 service groups, §4). Two write-boundary violations and one naming collision are flagged
> (§5). The cross-reference to L1 schema ownership is in §6. The parent needs to decide:
> (1) incident/postmortem write owner, (2) search read path post-migration, (3) schema names
> for seven stores not yet named in L1, and (4) first pilot scope and rollback path.
> Consultation-svc is the suggested first pilot (§9) — sole write owner, no cross-service
> volume deps, pilot task already assigned.
