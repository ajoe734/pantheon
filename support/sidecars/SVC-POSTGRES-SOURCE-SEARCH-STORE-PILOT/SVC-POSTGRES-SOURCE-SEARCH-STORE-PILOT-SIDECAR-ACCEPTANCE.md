# SVC-POSTGRES-SOURCE-SEARCH-STORE-PILOT Acceptance Packet

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `SVC-POSTGRES-SOURCE-SEARCH-STORE-PILOT-SIDECAR-ACCEPTANCE`
**Helper parent:** `SVC-POSTGRES-SOURCE-SEARCH-STORE-PILOT` - Add optional Postgres store pilot for source-ingest and search
**Parent owner:** `Claude`
**Parent reviewer:** `Codex2`
**Prepared by:** `Codex2`
**Date:** `2026-04-29`
**Packet status:** review approved; finalized for parent-owner disposition
**Review record:** `support/sidecars/SVC-POSTGRES-SOURCE-SEARCH-STORE-PILOT/review-claude-20260429.md`

> Scope constraint: support artifact only. This packet does not edit L1 canonical truth,
> service runtime code, compose defaults, registry behavior, or database policy. It packages
> acceptance criteria, dependency map, implementation guardrails, and reviewer checks for the
> parent owner to accept, amend, or ignore.

## 1. Purpose

This packet reduces restart cost for the parent task by turning the existing migration map and
current source/search code evidence into a reviewable implementation checklist.

The parent slice should prove an opt-in Postgres-backed store path for the existing
source-ingest/search durability boundary while preserving the current JSONL default. The key
ownership rule is unchanged: `source-ingest` owns source evidence writes; `search-svc` may own
query/index snapshot refs and may read evidence through a read-only boundary, but it must not
write source evidence rows.

## 2. Parent Task Truth

From task-scoped state on 2026-04-29:

| Field | Value |
|---|---|
| Parent task | `SVC-POSTGRES-SOURCE-SEARCH-STORE-PILOT` |
| Owner | `Claude` |
| Reviewer | `Codex2` |
| Status | `in_progress` |
| Phase | `Production Readiness / Data Ownership` |
| Formal artifacts | `services/source_ingestion`, `services/search`, `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md`, `docker-compose.yml` |

Recorded parent acceptance:

1. optional Postgres store can be enabled by env without breaking JSONL default
2. source evidence write owner remains source-ingest
3. search reads durable evidence through owned boundary or read-only contract
4. tests cover JSONL default and Postgres pilot path

## 3. Dependency Map

| Dependency | Status | Relevant output for this parent |
|---|---|---|
| `SVC-DATA-OWNERSHIP-MIGRATION-MAP` | `done` | Names source ingest plus search as P1 after consultation; maps `source-ingest` to `source.*` evidence/ingest tables and `search-svc` to search snapshot refs only. |
| `SVC-SOURCE-INGEST-EXTERNAL-FETCH-BASELINE` | `done` | Adds bounded `static_records` and `external_feed` fetch modes, DLQ/audit/watermark behavior, and durable evidence refs that the Postgres pilot must preserve. |
| `SVC-SEARCH-DURABLE-COMPAT-QUARANTINE` | `done` | Makes durable no-document search the normal path; request documents are compatibility-only and must not become the Postgres persistence model. |

The parent should not proceed as if Postgres is the new default. It should add an explicit opt-in
backend that can be rolled back to JSONL by env change.

## 4. Current Store Boundary

### 4.1 source-ingest writes

Current JSONL paths and code evidence:

| Store | Default path/env | Code evidence | Owner rule |
|---|---|---|---|
| Ingest run and watermark log | `SOURCE_INGEST_STORE_PATH`, default under `SOURCE_INGEST_DATA_DIR/ingest_schedule.jsonl` | `services/source_ingestion/scheduler.py` `JsonlIngestScheduleStore` | `source-ingest` writes ingest runs and watermarks. |
| Connector config and fetch state | `SOURCE_INGEST_CONNECTOR_STORE_PATH`, default `connector_config.jsonl` | `services/source_ingestion/configured.py` `JsonlConfiguredConnectorStore` | `source-ingest` writes connector config and fetch state. |
| Source evidence repository | `SOURCE_INGEST_EVIDENCE_STORE_PATH`, default `source_evidence.jsonl` | `services/source_ingestion/main.py`, `JsonlEvidenceRepository` | `source-ingest` writes source records, evidence items, bundles, and knowledge objects. |
| DLQ spill | `SOURCE_INGEST_DLQ_PATH`, default `source_ingest_dlq.jsonl` | `services/source_ingestion/main.py`, `DeadLetterQueue` | `source-ingest` writes failed/rejected ingest events. |
| Audit log | `SOURCE_INGEST_AUDIT_PATH`, default `source_ingest_audit.jsonl` | `services/source_ingestion/main.py` `_append_audit_actions` | `source-ingest` writes source-ingest audit actions. |

### 4.2 search writes and reads

Current JSONL paths and code evidence:

| Store/read | Default path/env | Code evidence | Owner rule |
|---|---|---|---|
| Search snapshot refs | `SEARCH_INDEX_STORE_PATH`, default `SEARCH_DATA_DIR/search-index.jsonl` | `services/search/index_store.py` `JsonlSearchIndexStore` | `search-svc` may write query snapshot refs. |
| Durable evidence read | `SEARCH_EVIDENCE_STORE_PATH`, compose maps to `/data/source-ingest/source_evidence.jsonl` | `services/search/main.py` `JsonlEvidenceRepository` | Read-only evidence boundary; `search-svc` must not write source evidence. |
| Request documents compat | Explicit flag or `/api/search/query/request-documents-compat` | `services/search/main.py` `_query_search` | Compatibility-only; should not be promoted into the Postgres store model. |

## 5. Proposed Acceptance Checklist

### AC-1: JSONL remains default

| Check | Required evidence |
|---|---|
| No default compose behavior change | `docker compose config --quiet` passes with no required Postgres env for source/search pilot. |
| Service imports still work without Postgres driver env | Source-ingest and search focused tests pass with no Postgres backend selected. |
| Existing JSONL env names still work | Tests exercise `SOURCE_INGEST_*_PATH`, `SEARCH_INDEX_STORE_PATH`, and `SEARCH_EVIDENCE_STORE_PATH` on temp JSONL files. |
| Health details remain truthful | `/health` reports active JSONL paths when JSONL backend is selected. |

### AC-2: Postgres is explicit opt-in

| Check | Required evidence |
|---|---|
| Backend selection is env-gated | A clear env such as `SOURCE_INGEST_STORE_BACKEND=postgres` and `SEARCH_STORE_BACKEND=postgres`, or an equivalent service-specific name, selects Postgres. Unset env selects JSONL. |
| DSN is required only for Postgres mode | Missing DSN fails closed in Postgres mode and does not affect JSONL mode. |
| Schema/bootstrap path is documented or implemented | Parent deliverable names how `source.*` and search snapshot tables are created or bootstrapped. |
| Rollback path is explicit | Rollback is env change back to JSONL/unset backend plus service restart; Postgres rows remain read-only for investigation. |

### AC-3: source-ingest remains write owner

| Check | Required evidence |
|---|---|
| Source evidence writes are isolated to source-ingest store code | Search code has no insert/update path for source records, evidence items, bundles, or knowledge objects. |
| Watermarks and DLQ writes remain source-ingest-owned | Postgres path preserves current scheduler behavior: failed fetches route to DLQ and do not advance watermarks. |
| Connector config and fetch state remain source-ingest-owned | Postgres path covers connector config/fetch state if that store is moved in the pilot; otherwise the deferred state is documented as JSONL-retained. |
| Audit/outbox equivalent is not bypassed | Current audit append behavior is preserved or explicitly mapped to audit tables owned by source-ingest/audit policy. |

### AC-4: search uses durable evidence through an owned read boundary

| Check | Required evidence |
|---|---|
| Durable no-document query stays normal path | `/api/search/query` without request documents reads durable evidence and creates search snapshot refs. |
| Request documents stay quarantined | Existing compat rejection/flag tests still pass. |
| Search read access is read-only | If search reads source Postgres tables directly, it uses a read-only role or read repository API; if it calls source-ingest, the API boundary is explicit. |
| Search only writes snapshot refs | Postgres search store writes only `governed_search_refs.v1`-style snapshot refs or equivalent result refs, not raw source evidence. |

### AC-5: Tests cover both paths

| Check | Required evidence |
|---|---|
| JSONL default path | Existing focused source-ingest/search tests pass without Postgres env. |
| Postgres source-ingest path | Tests cover successful ingest, reload/replay, watermark update, failed fetch DLQ, and no watermark advance on failed fetch. |
| Postgres search path | Tests cover durable query, snapshot replay, access/license filtering, and request-document compat rejection. |
| Compose/default guard | Compose config test or equivalent confirms default services do not require Postgres pilot env. |

## 6. Implementation Guardrails For Parent Owner

These are support recommendations, not canonical decisions:

1. Prefer a small store abstraction layer over branching inside endpoint handlers. Current seam points are `JsonlIngestScheduleStore`, `JsonlConfiguredConnectorStore`, `JsonlEvidenceRepository`, and `JsonlSearchIndexStore`.
2. Keep bootstrap/import one-way for this pilot. Avoid dual-write unless a separate reconciliation task owns correctness and rollback.
3. Do not make `search-svc` a source evidence writer to simplify tests. That would violate the dependency map and database ownership policy.
4. Keep request-document compatibility in the current explicit flag/route path. Do not use compat payloads to populate Postgres durable evidence.
5. If the parent chooses a reduced pilot scope, the packet should say exactly which stores remain JSONL, especially source-ingest DLQ/audit/connector state.
6. If compose gets Postgres pilot env examples, keep them profile-gated or disabled by default.

## 7. Suggested Focused Verification

Parent owner should replace or extend these with exact commands used:

```bash
python3 -m pytest services/source_ingestion/test_service.py services/source_ingestion/tests/test_ingest_run.py
python3 -m pytest services/search/tests/test_http_service.py services/search/tests/test_contracts.py
python3 -m pytest services/source_ingestion/test_compose_activation.py services/search/tests/test_service_activation_contract.py
docker compose config --quiet
git diff --check -- services/source_ingestion services/search docker-compose.yml
```

If Postgres integration tests require a live database, record the exact env and command separately,
for example a profile-gated compose invocation or a pytest marker. Do not let that replace the
JSONL default verification.

## 8. Reviewer Checklist For Codex2

When the parent returns for review, check these first:

| Review area | Pass condition |
|---|---|
| Default safety | No Postgres env is needed for existing JSONL tests or default compose config. |
| Ownership | Search has no write path into source evidence tables or repositories. |
| Durability parity | Source-ingest Postgres path preserves replay, watermarks, DLQ, audit, evidence refs, and access/license metadata. |
| Search parity | Search Postgres path preserves durable query, snapshot refs, replay, access/license filtering, and compat quarantine. |
| Config clarity | Backend env names, DSN env names, bootstrap/import instructions, and rollback path are documented. |
| Scope hygiene | L1 canonical docs are not broadened unless parent task explicitly owns a canonical truth change. |

## 9. Handoff Notes

Claude approved this packet on 2026-04-29 as support material. Reviewer disposition:

- approved as useful acceptance/dependency support
- scope-clean: no canonical/runtime changes
- ownership boundary acceptable as support guidance

Parent owner remains responsible for deciding whether to absorb this packet into the implementation
or task handoff.
