# SVC-SOURCE-SEARCH-PROD-HARDENING Sidecar Acceptance Packet

- Task: `SVC-SOURCE-SEARCH-PROD-HARDENING-SIDECAR-ACCEPTANCE`
- Parent task: `SVC-SOURCE-SEARCH-PROD-HARDENING`
- Parent status: `done` as of 2026-04-30T09:10:51Z (`e93aaf9`)
- Sidecar owner: Claude
- Sidecar reviewer: Codex (reassigned from Codex2 at 2026-04-30T09:17:41Z)
- Prepared: 2026-04-30
- Scope: support artifact only; no L1 canonical truth, canonical contract, runtime, registry, or governance implementation changes.

---

## Purpose

This packet provides the parent task owner (Codex2) with a structured acceptance checklist, dependency map, and closeout evidence summary for the source/search production hardening task. It confirms that all five acceptance criteria are satisfied and that the implementation is consistent with the L1 database ownership and storage architecture policies.

The parent task has already been finalized as `done`; this packet is post-closeout support material for audit, handoff, and optional future absorption by the parent owner. It is not an instruction to rerun parent closeout.

This is sidecar material: it does not approve or activate production deployment, change policy, or replace the parent reviewer's judgment.

---

## Sources Read

- `AI_COLLABORATION_GUIDE.md`
- `ai-status.json`
- `.orchestrator/task-briefs/svc_source_search_prod_hardening_sidecar_acceptance.md` (sidecar context)
- `ai-task-archive/tasks/SVC-SOURCE-SEARCH-PROD-HARDENING.json` — parent closeout record
- commit `b99ebcb` — full diff reviewed
- commit `e93aaf9` — parent closeout evidence
- `services/source_search_posture.py` — posture module
- `services/test_source_search_posture.py` — posture unit tests
- `services/source_ingestion/main.py` — posture integration
- `services/search/main.py` — posture integration
- `services/source_ingestion/pg_store.py` — idempotency evidence
- `scripts/smoke_source_search_prod_posture.py` — smoke script
- `docs/deployment/source-search-prod-hardening.md` — deployment docs
- `docker-compose.yml` — compose wiring
- `env/prod-control.env.example` — prod env
- `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` (L1 — database ownership)

---

## Non-Scope Guardrails

- Do not use this packet to claim production readiness or recommend enabling production env. Posture hardening is a necessary but not sufficient condition for production deployment.
- Do not modify `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md`, `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`, or other L1 truth from this slice.
- Do not treat the new `source_search_posture.py` module as a migration gate — it is a startup enforcement check, not a migration orchestrator.
- Do not activate `PANTHEON_SOURCE_SEARCH_POSTURE=production` on any running stack from this closeout. Production posture activation is an operator action.

---

## Dependency Map

| Dependency | Status | Relevance to parent acceptance |
|---|---|---|
| `SVC-SOURCE-CRAWL-FRONTIER-SCHEDULER` | done | Frontier scheduler produces `source_dedupe_key`-tagged records. Posture hardening must not break scheduler-to-ingest contract. Confirmed: `require_source_search_posture` runs at startup and raises before any scheduler interaction. |
| `SVC-SOURCE-EVIDENCE-NORMALIZATION` | done | Evidence normalization sets `evidence_dedupe_key` for idempotent upserts. The posture check does not touch evidence normalization code. `source_ingestion/pg_store.py` idempotency remains intact. |
| `SVC-SEARCH-INDEXING-PIPELINE` | done | Index pipeline depends on `SEARCH_DURABLE_INDEX_ONLY` and `SEARCH_INDEX_STORE_BACKEND`. Posture enforcement now validates these at startup before the pipeline can run. No regression to existing pipeline logic. |
| `SVC-SEARCH-RETRIEVAL-AND-CUTOFF` | done | Retrieval depends on Postgres index store. Posture enforcement confirms `SEARCH_INDEX_STORE_BACKEND=postgres` is required in staging/prod. Consistent with prior retrieval cutoff hardening. |
| `SVC-SOURCE-SEARCH-OPS-BFF` | done | BFF ops surface already consumes `/health` and `/metrics` from both services. The new `posture_alert_count` metric and `source_search_posture` health field are additive and do not break existing BFF routes. |

All five dependency tasks are in `done` state. No open dependency blocks.

---

## Parent Acceptance Checklist

### Criterion 1 — Staging/prod env rejects jsonl-only source/search backend

**Required evidence:**
- `PANTHEON_SOURCE_SEARCH_POSTURE=staging|prod|production` triggers posture enforcement.
- `SOURCE_INGEST_EVIDENCE_BACKEND != postgres` causes startup failure with clear error.
- `SEARCH_INDEX_STORE_BACKEND != postgres` and/or `SEARCH_EVIDENCE_BACKEND != postgres` causes startup failure.
- Dev mode (default) continues to work with JSONL backends.

**Observed evidence from commit b99ebcb:**
- `ENFORCED_MODES = {"staging", "prod", "production"}` — covers standard naming variants.
- `validate_source_search_posture` rejects non-postgres backends in enforced modes.
- `require_source_search_posture` raises `RuntimeError` at module load; services fail to start.
- `docker-compose.yml` defaults `PANTHEON_SOURCE_SEARCH_POSTURE=dev` — local dev is unaffected.
- `test_source_ingest_production_posture_requires_postgres_and_object_store` and `test_search_production_posture_rejects_request_document_mode` cover the rejection paths.

**Verdict: SATISFIED**

---

### Criterion 2 — Postgres and object store ownership documented and enforced

**Required evidence:**
- `DATABASE_URL` must be a Postgres DSN (`postgresql://` or `postgres://` prefix).
- All four object-store env vars (`PANTHEON_S3_ENDPOINT`, `PANTHEON_ARTIFACT_BUCKET`, `PANTHEON_S3_ACCESS_KEY`, `PANTHEON_S3_SECRET_KEY`) required in enforced modes.
- `env/prod-control.env.example` lists all required env vars for the production posture.
- Deployment docs describe the complete required posture.

**Observed evidence from commit b99ebcb:**
- `validate_source_search_posture` checks `DATABASE_URL.startswith(("postgresql://", "postgres://"))`.
- All four object-store keys are validated via `object_store_configured = all(...)`.
- Errors include per-key messages: `"PANTHEON_S3_ENDPOINT is required for staging/prod source-search object-store posture"`.
- `env/prod-control.env.example` includes `PANTHEON_SOURCE_SEARCH_POSTURE=production` and all required values.
- `docs/deployment/source-search-prod-hardening.md` lists the complete posture requirements.

**Verdict: SATISFIED**

---

### Criterion 3 — Health / live / ready / metrics expose consistent service state

**Required evidence:**
- `/readyz` returns non-200 when posture has errors.
- `/metrics` exposes a `posture_alert_count` counter that is 0 when posture is ok.
- `/health` exposes `source_search_posture` field with status, enforced, backends, and object_store_configured.
- BFF ops surface can read posture state via existing `/health` route.

**Observed evidence from commit b99ebcb:**
- Both services expose `PRODUCTION_POSTURE.to_dict()` in health `dependencies` and `details` dicts.
- `PRODUCTION_POSTURE.alert_count()` wired to `posture_alert_count` metric in both services.
- Since `require_` raises on posture errors, a running service always has `alert_count() == 0` — this is correct fail-closed semantics; a misconfigured service never reaches a running state.
- Smoke script verifies `posture_alert_count=0`, `enforced=true`, and `object_store_configured=true` via live HTTP checks.

**Verdict: SATISFIED**

---

### Criterion 4 — Idempotency keys protect ingest/index/reindex commands

**Required evidence:**
- Ingest operations use dedupe keys to prevent duplicate evidence records.
- Index/reindex commands use idempotent upsert semantics (ON CONFLICT behavior).
- Posture hardening does not regress existing idempotency guarantees.

**Observed evidence (pre-existing, confirmed unmodified by b99ebcb):**
- `services/source_ingestion/main.py` lines 507–518: `source_dedupe_key` lookup in `evidence_repository.get_source_record_by_dedupe_key` before insert.
- `services/source_ingestion/pg_store.py`: `_source_dedupe_index` and `_evidence_dedupe_index` maintain in-memory dedupe state backed by Postgres.
- `services/source_ingestion/test_service.py` test `test_source_evidence_normalization_sets_canonical_refs_and_dedupes_owner` verifies dedup behavior.
- Commit b99ebcb does not touch `pg_store.py`, the dedupe index logic, or the evidence normalization path. No regression introduced.

**Verdict: SATISFIED (pre-existing, unmodified)**

---

### Criterion 5 — End-to-end smoke covers connector to evidence to index to BFF query

**Required evidence:**
- A smoke script covers the full source→evidence→index→BFF query path.
- Production-specific posture is verified by a dedicated smoke check.
- Both smoke paths are documented for operators.

**Observed evidence from commit b99ebcb:**
- `scripts/smoke_source_search_prod_posture.py` runs production posture smoke: `/readyz`, `/metrics` (`posture_alert_count`), `/health` (enforced + object_store_configured + backends) on both services.
- `docs/deployment/source-search-prod-hardening.md` documents both postures:
  - New prod posture smoke: `scripts/smoke_source_search_prod_posture.py`
  - Full E2E connector→evidence→index→BFF: broader honest-stack smoke (pre-existing, referenced)
- The broader honest-stack smoke already covered in prior tasks (`SVC-SOURCE-SEARCH-OPS-BFF`, `SVC-SEARCH-RETRIEVAL-AND-CUTOFF`) covers the full E2E path.

**Verdict: SATISFIED**

---

## Verification Summary

The following verification was recorded in commit b99ebcb's body:

```
pytest -q services/test_source_search_posture.py
  → posture unit tests

pytest -q services/source_ingestion/test_compose_activation.py
       services/search/tests/test_service_activation_contract.py
       services/search/tests/test_http_service.py
       services/source_ingestion/test_service.py
  → compose contract + HTTP service tests

pytest -q services/source_ingestion/test_postgres_store.py
       services/search/test_postgres_store.py
       services/search/test_index_pipeline.py
  → Postgres store + index pipeline tests

docker compose config >/tmp/pantheon-compose-config.out
  → compose configuration validation

python3 -m py_compile services/source_search_posture.py
                       scripts/smoke_source_search_prod_posture.py
  → syntax validation of new files
```

Total tests run: 72+ (30 source/search HTTP+compose + 42 Postgres/pipeline). All passing per Codex2's verification note.

Syntax of new files confirmed in this session:
```
python3 -m py_compile services/source_search_posture.py scripts/smoke_source_search_prod_posture.py
→ syntax ok
```

---

## Parent Archive Note for Codex2

All five acceptance criteria are met and verified. The implementation is:

1. Fail-closed at startup for staging/prod posture modes
2. Non-breaking for dev/JSONL rollback environments
3. Consistent with the L1 database ownership policy (Postgres DSN + object store required)
4. Properly instrumented in health/metrics endpoints
5. Idempotency-safe (pre-existing, unmodified)
6. Documented with deployment docs and smoke script

The parent task was already finalized in closeout commit `e93aaf9`; do not run `scripts/ai-status.sh done` for `SVC-SOURCE-SEARCH-PROD-HARDENING` again. Parent owner Codex2 may use this packet as supporting evidence or a follow-up handoff reference, but it does not reopen or replace the archived parent delivery record.

For this sidecar task, the next lifecycle action after reviewer approval is owner finalization by Claude using the normal task closeout process.

---

## Sidecar Reviewer Addendum

- Reviewer: Codex
- Review date: 2026-04-30
- Outcome: APPROVED

Codex reviewed the sidecar packet after the review dispatch was reassigned from Codex2. The packet is support-only, respects the non-scope guardrails, and accurately summarizes the parent implementation and dependency evidence after factual corrections for the current parent status and reviewer assignment.

Corrections applied during review:

- Updated the sidecar reviewer from Codex2 to Codex and recorded the reassignment timestamp.
- Marked the parent task as already `done` in closeout commit `e93aaf9`.
- Replaced the accidentally listed OpenClaw task brief with `.orchestrator/task-briefs/svc_source_search_prod_hardening_sidecar_acceptance.md`.
- Converted the stale parent closeout instructions into a parent archive note.

Verification run during this sidecar review:

```bash
python3 -m py_compile services/source_search_posture.py scripts/smoke_source_search_prod_posture.py
python3 -m pytest -q services/test_source_search_posture.py
```

Result: `5 passed in 0.08s`; `py_compile` completed successfully. A trailing-whitespace scan of this packet completed cleanly.

No blocking issues remain for this sidecar packet. Claude should finalize the sidecar task with a task-scoped support-artifact commit; parent owner Codex2 can decide whether to absorb this packet as a follow-up reference.

---

## Non-Goals of This Packet

- Does NOT activate `PANTHEON_SOURCE_SEARCH_POSTURE=production` on any stack
- Does NOT modify `source_search_posture.py`, `main.py`, or any service implementation
- Does NOT modify any L1 canonical policy files
- Does NOT create new BFF routes or service endpoints
- Does NOT alter the database ownership policy or storage architecture docs
- Does NOT constitute a production deployment decision

All findings are advisory. The parent task owner (Codex2) decides whether to proceed with closeout finalization.
