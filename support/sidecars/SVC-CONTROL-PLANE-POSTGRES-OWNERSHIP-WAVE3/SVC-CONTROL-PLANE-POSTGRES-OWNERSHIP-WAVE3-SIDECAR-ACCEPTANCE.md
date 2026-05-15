# Acceptance Packet: SVC-CONTROL-PLANE-POSTGRES-OWNERSHIP-WAVE3

Sidecar task: SVC-CONTROL-PLANE-POSTGRES-OWNERSHIP-WAVE3-SIDECAR-ACCEPTANCE
Parent task: SVC-CONTROL-PLANE-POSTGRES-OWNERSHIP-WAVE3
Prepared by: Claude
Reviewer: Codex
Date: 2026-04-30
Status: **REVIEW APPROVED**

---

## 1. Purpose

This packet supports the acceptance and handoff of the Wave 3 Postgres owner-store migration for the Pantheon control-plane services. It provides:

- An acceptance checklist with evidence for each criterion
- A dependency map (upstream and downstream)
- An implementation inventory (13 tables across 7 services)
- Verification commands and pass/fail summary
- Quality notes and known limitations
- Handoff instructions for the reviewer (Codex)

This is a support artifact only. It does not modify any canonical truth documents or runtime implementations.

---

## 2. Dependency Map

### 2.1 Upstream (blocking dependencies)

| Task | Status | Notes |
|---|---|---|
| SVC-POSTGRES-PRODUCTION-OWNERSHIP-WAVE2 | done | Established `PostgresJsonOwnerStore` foundation primitive, Wave2 inventory for 8 services |

Wave3 depends on the `PostgresJsonOwnerStore` base class and the `PostgresJsonReadOnlyStore` boundary primitive delivered in Wave2. Both are in `services/foundation/postgres_json_store.py`.

### 2.2 Downstream (tasks unblocked by Wave3)

| Task | Status | Dependency reason |
|---|---|---|
| SVC-MEMORY-AUTHZ-RETENTION-REPLAY-HARDENING | todo | Requires memory-svc Postgres owner store to be available before implementing authz / retention / replay hardening |
| SVC-BFF-SERVICE-BACKED-READ-STORE-CUTOFF | done | BFF read clients for governance, capital, memory stores benefit from Postgres path being stable |

---

## 3. Acceptance Criteria Checklist

### ✓ 1. Governance / capital / incidents / postmortems / promotion / reconciliation / memory stores are code-inventoried

Evidence: `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` section 4.2 enumerates all 13 tables across 7 services. Each row includes: service, dev fallback path, Postgres owner table, write owner, and non-owner read contract.

### ✓ 2. Each store has a Postgres owner schema/table and env-gated builder

| Service | Implementation file | Env gate | Postgres tables |
|---|---|---|---|
| governance-svc (decisions) | `services/governance/pg_store.py` → `PostgresApprovalDecisionStore` | `GOVERNANCE_STORE_BACKEND` | `governance.approval_decisions` |
| governance-svc (audit) | `services/governance/pg_store.py` → `PostgresGovernanceAuditStore` | `GOVERNANCE_AUDIT_BACKEND` | `governance.audit_events` |
| capital-pool-svc (pools) | `services/capital/pg_store.py` → `PostgresCapitalPoolStore` | `CAPITAL_STORE_BACKEND` | `capital.capital_pools` |
| capital-pool-svc (bindings) | `services/capital/pg_store.py` → `PostgresPersonaCapitalBindingStore` | `CAPITAL_STORE_BACKEND` | `capital.persona_capital_bindings` |
| capital-pool-svc (audit) | `services/capital/pg_store.py` → `PostgresCapitalAuditStore` | `CAPITAL_AUDIT_BACKEND` | `capital.audit_events` |
| incident-svc | `services/incident/pg_store.py` → `PostgresIncidentStore` | `INCIDENT_STORE_BACKEND` | `incident.incident_cases` |
| postmortem-svc | `services/incident/pg_store.py` → `PostgresIncidentStore` | `POSTMORTEM_STORE_BACKEND` | `incident.postmortems` |
| promotion-svc | `services/promotion/pg_store.py` | `PROMOTION_STORE_BACKEND` | `promotion.approval_decisions`, `promotion.deployment_plans`, `promotion.deployment_plan_extensions` |
| reconciliation-drift-svc | `services/reconciliation-drift/store.py` → `PostgresReconciliationDriftStore` | `RECONCILIATION_DRIFT_STORE_BACKEND` | `reconciliation_drift.drift_evaluations`, `reconciliation_drift.alert_handoffs` |
| memory-svc | `services/memory/institutional_memory_store.py` → `PostgresInstitutionalMemoryStore` | `PANTHEON_MEMORY_STORE_BACKEND` | `memory.institutional_memory_entries` |

All builders follow the same pattern: default to `json` when the env var is unset or `"json"`; raise `ValueError` for unknown values; resolve DSN from service-specific `*_DSN` then fall back to `DATABASE_URL`.

Total: **13 tables across 7 services**.

### ✓ 3. Staging/prod env examples select Postgres without cross-service volume writes

Evidence: `env/prod-control.env.example` sets all 9 Wave3 backend vars to `postgres` with:

```
DATABASE_URL=postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon
```

Variables set: `GOVERNANCE_STORE_BACKEND`, `GOVERNANCE_AUDIT_BACKEND`, `CAPITAL_STORE_BACKEND`, `CAPITAL_AUDIT_BACKEND`, `INCIDENT_STORE_BACKEND`, `POSTMORTEM_STORE_BACKEND`, `PROMOTION_STORE_BACKEND`, `RECONCILIATION_DRIFT_STORE_BACKEND`, `PANTHEON_MEMORY_STORE_BACKEND`.

No cross-service volume mount is required to activate the Postgres path in staging/prod.

### ✓ 4. Dev JSON fallback and rollback remain available

All builders default to the JSON/JSONL implementation when the backend env var is absent or set to `json`/`jsonl`. Compose keeps local data directories mounted so operators can roll back by changing only the env vars — no data migration required.

### ✓ 5. Write ownership and read-only boundary tests are added

`services/foundation/tests/test_control_plane_postgres_owner_stores.py` covers:

- `test_postgres_json_owner_store_read_only_boundary`: creates an owner store, writes a record, creates a `read_only=True` store over the same table, confirms `get` succeeds and `put` raises `PermissionError` with the "writes must go through \<service\>" message.
- `test_wave3_postgres_builders_are_env_gated`: sets all 9 backend env vars to `postgres`, calls every Wave3 builder, asserts each returns the Postgres subclass. Post-run DDL check confirms `governance`, `capital`, `incident`, `promotion`, `memory`, and `reconciliation_drift` schemas all appear in CREATE statements.

### ✓ 6. Focused tests and compose config pass

Verification command:

```
python3 -m pytest \
  services/foundation/tests/test_control_plane_postgres_owner_stores.py \
  services/governance/test_governance_api.py \
  services/capital/test_service.py \
  services/incident/test_incident.py \
  services/incidents/test_main_routes.py \
  services/postmortems/test_main_routes.py \
  services/promotion/test_service.py \
  services/memory/test_institutional_memory_store.py \
  services/memory/test_main.py \
  services/reconciliation-drift/tests/test_reconciliation_drift_http_service.py \
  services/reconciliation-drift/tests/test_reconciliation_drift_compose_activation.py
```

Result: **189 passed**

Compose validation:

```
docker compose config --quiet               # exit 0
docker compose -f docker-compose.control.yml config --quiet   # exit 0
```

---

## 4. Implementation Quality Notes

### 4.1 Read-modify-write pattern

`PostgresCapitalPoolStore`, `PostgresPersonaCapitalBindingStore`, and `PostgresInstitutionalMemoryStore` each call `_refresh_from_postgres()` before every mutating operation then delegate to the JSON-backed parent `_save()`. This is safe under the single-writer ownership contract enforced by policy; acceptable for v1 throughput requirements.

### 4.2 Shared incident/postmortem store class

`PostgresIncidentStore` manages both `incident.incident_cases` (owner: incident-svc) and `incident.postmortems` (owner: postmortem-svc) in a single class. Each table has its own `PostgresJsonOwnerStore` instance with the correct `owner_service` label, preserving the write-boundary contract. The shared class is an implementation convenience, not a policy exception.

### 4.3 Memory schema validation on hydration

`PostgresInstitutionalMemoryStore._refresh_from_postgres()` re-runs both semantic and JSON schema validators on every hydrated record. This adds latency per write cycle but guarantees the in-memory state is always canonical and schema-valid.

### 4.4 DSN fallback chain

All builders check a service-specific DSN first (`GOVERNANCE_STORE_DSN`, `CAPITAL_STORE_DSN`, `INCIDENT_STORE_DSN`, `PROMOTION_STORE_DSN`, `RECONCILIATION_DRIFT_STORE_DSN`, `MEMORY_STORE_DSN`) before falling back to `DATABASE_URL`. This allows stricter role separation (distinct DB users per service) without breaking dev/CI defaults.

### 4.5 Known limitation — no live Postgres integration test

All foundation tests use an in-memory SQLite backend (`:memory:` via SQLAlchemy). The `test_wave3_postgres_builders_are_env_gated` test verifies builder dispatch and DDL generation but does not execute against a live Postgres instance. A live integration test suite against the compose `postgres` container is a follow-up item, not a Wave3 blocker.

---

## 5. Delivery Metadata

| Field | Value |
|---|---|
| Parent task | SVC-CONTROL-PLANE-POSTGRES-OWNERSHIP-WAVE3 |
| Terminal status | done |
| Owner | Codex |
| Reviewer | Claude |
| Review decision | APPROVED |
| Delivery commit | `433b39e717020288a5174092ffbbf3c121b0c6b6` |
| Commit subject | SVC-CONTROL-PLANE-POSTGRES-OWNERSHIP-WAVE3 finalize owner stores |
| Branch | backend-dev-publish-20260429 |
| Push status | ahead (local, not yet pushed to remote) |
| Archived at | 2026-04-30T02:41:06Z |

---

## 6. Handoff to Reviewer (Codex)

This packet has been reviewed and approved by Codex. No canonical truth files were modified by this sidecar task. The packet may be absorbed into the parent task archive or used as a standalone acceptance reference.

**Reviewer actions completed:**

1. Confirmed inventory table in section 3 matches `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` section 4.2.
2. Spot-checked builder env-gate logic in `services/governance/pg_store.py` and `services/memory/institutional_memory_store.py`.
3. Confirmed the parent archive records the 189-test verification.
4. Approved the sidecar task for owner closeout.

---

## 7. Reviewer Disposition

Reviewer: Codex
Reviewed at: 2026-04-30
Decision: **APPROVED**

Review scope:

- Confirmed `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` section 4.2 contains the 13-table / 7-service Wave3 inventory reflected in this packet.
- Spot-checked `services/governance/pg_store.py` and `services/memory/institutional_memory_store.py`; both expose env-gated Postgres builders with service-specific DSN fallback to `DATABASE_URL`.
- Confirmed `env/prod-control.env.example` selects the Wave3 Postgres backend vars and shared `DATABASE_URL`.
- Confirmed parent task archive records `SVC-CONTROL-PLANE-POSTGRES-OWNERSHIP-WAVE3` as `done` with delivery commit `433b39e717020288a5174092ffbbf3c121b0c6b6` and the recorded 189-test verification.
- Updated the downstream map to reflect that `SVC-BFF-SERVICE-BACKED-READ-STORE-CUTOFF` is now archived as `done` at `2026-04-30T03:05:56Z`.

No canonical truth or runtime implementation files were modified by this reviewer pass.
