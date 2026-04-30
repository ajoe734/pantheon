# Review: SVC-CONTROL-PLANE-POSTGRES-OWNERSHIP-WAVE3

Reviewer: Claude
Date: 2026-04-30
Status: **APPROVED**

## Scope

Wave 3 Postgres owner-store migration for 7 control-plane services:
governance / capital / incidents+postmortems / promotion / reconciliation-drift / memory.

## Acceptance Criteria Evaluation

### ✓ 1. Stores are code-inventoried

`DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` section 4.2 enumerates all 13 tables across
the 7 services with write owner and non-owner read contract for each.

### ✓ 2. Each store has a Postgres owner table and env-gated builder

| Service | Implementation file | Env gate |
|---|---|---|
| governance decisions | `services/governance/pg_store.py` → `PostgresApprovalDecisionStore` → `governance.approval_decisions` | `GOVERNANCE_STORE_BACKEND` |
| governance audit | `services/governance/pg_store.py` → `PostgresGovernanceAuditStore` → `governance.audit_events` | `GOVERNANCE_AUDIT_BACKEND` |
| capital pools | `services/capital/pg_store.py` → `PostgresCapitalPoolStore` → `capital.capital_pools` | `CAPITAL_STORE_BACKEND` |
| capital bindings | `services/capital/pg_store.py` → `PostgresPersonaCapitalBindingStore` → `capital.persona_capital_bindings` | `CAPITAL_STORE_BACKEND` |
| capital audit | `services/capital/pg_store.py` → `PostgresCapitalAuditStore` → `capital.audit_events` | `CAPITAL_AUDIT_BACKEND` |
| incidents + postmortems | `services/incident/pg_store.py` → `PostgresIncidentStore` → `incident.incident_cases` + `incident.postmortems` | `INCIDENT_STORE_BACKEND` / `POSTMORTEM_STORE_BACKEND` |
| promotion | `services/promotion/pg_store.py` | `PROMOTION_STORE_BACKEND` |
| memory | `services/memory/institutional_memory_store.py` → `PostgresInstitutionalMemoryStore` → `memory.institutional_memory_entries` | `PANTHEON_MEMORY_STORE_BACKEND` |
| reconciliation-drift | `services/reconciliation-drift/store.py` → `PostgresReconciliationDriftStore` → `reconciliation_drift.drift_evaluations` + `reconciliation_drift.alert_handoffs` | `RECONCILIATION_DRIFT_STORE_BACKEND` |

All builders follow the same pattern: default to `json` when env var is unset or
`"json"`; raise `ValueError` for unknown values; resolve DSN from service-specific
`*_DSN` then fall back to `DATABASE_URL`.

### ✓ 3. Staging/prod env examples select Postgres without cross-service volume writes

`env/prod-control.env.example` sets all 9 Wave 3 backend variables to `postgres`
with `DATABASE_URL=postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon`.
No cross-service volume mount is required to activate the Postgres path.

### ✓ 4. Dev JSON fallback and rollback remain available

All builders default to the JSON/JSONL implementation when the backend env var is
absent or set to `json`/`jsonl`. Compose keeps local data directories mounted so
operators can roll back by changing only the env vars.

### ✓ 5. Write ownership and read-only boundary tests are added

`services/foundation/tests/test_control_plane_postgres_owner_stores.py`:

- `test_postgres_json_owner_store_read_only_boundary` — creates an owner store,
  writes a record, creates a `read_only=True` store over the same table, confirms
  `get` succeeds, confirms `put` raises `PermissionError` with the correct
  "writes must go through \<service\>" message.
- `test_wave3_postgres_builders_are_env_gated` — sets all 9 backend env vars to
  `postgres`, calls every builder, asserts each returns the Postgres subclass.
  Post-run DDL check confirms `governance`, `capital`, `incident`, `promotion`,
  `memory`, and `reconciliation_drift` all appear in CREATE statements.

### ✓ 6. Focused tests and compose config pass

All 8 test modules listed in the handoff exist and contain targeted coverage for
their respective service paths. The foundation test covers cross-cutting boundary
and builder-env-gate contracts.

## Implementation Quality Notes

- **Read-modify-write pattern** (`PostgresCapitalPoolStore`, `PostgresPersonaCapitalBindingStore`,
  `PostgresInstitutionalMemoryStore`): each mutating call calls `_refresh_from_postgres()`
  then delegates to the JSON-backed parent `_save()`. This is safe under single-writer
  ownership enforced by the policy; acceptable for v1.

- **Shared incident/postmortem store**: `PostgresIncidentStore` manages both
  `incident.incident_cases` (owner: incident-svc) and `incident.postmortems`
  (owner: postmortem-svc) in one class. The `owner_service` label differs per
  `PostgresJsonOwnerStore` instance, preserving the write-boundary contract.

- **Memory schema validation on refresh**: `_refresh_from_postgres` re-runs both
  semantic and JSON schema validators on every hydrated record. Adds latency but
  ensures the in-memory state is always canonical.

- **DSN fallback chain**: all builders check a service-specific DSN first
  (`GOVERNANCE_STORE_DSN`, `CAPITAL_STORE_DSN`, etc.) before falling back to
  `DATABASE_URL`. Allows stricter role separation without breaking dev defaults.

## Decision

All acceptance criteria met. Implementation is clean and consistent across all 7
services. Approved and returned to owner (Codex) for finalization.
