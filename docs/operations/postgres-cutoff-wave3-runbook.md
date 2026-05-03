# Postgres Cutoff Wave 3 — Control-Plane Owner Store Migration Runbook

Task: SVC-BLUEPRINT-POSTGRES-CUTOFF-WAVE3
Status: delivered
Last updated: 2026-05-03

## 1. Scope

Wave 3 migrates the remaining control-plane and data-plane owner stores from
JSON/JSONL baseline to PostgreSQL write ownership. Dev/local environments keep
JSONL rollback; staging and production must use the Postgres backend or fail
closed at startup.

### Services migrated

| Service | Postgres schema | Write owner | Env gate |
|---|---|---|---|
| governance-svc | `governance.approval_decisions`, `governance.audit_events` | governance-svc | `GOVERNANCE_STORE_BACKEND=postgres` |
| capital-pool-svc | `capital.capital_pools`, `capital.persona_capital_bindings`, `capital.audit_events` | capital-pool-svc | `CAPITAL_STORE_BACKEND=postgres` |
| incidents-svc | `incident.incident_cases` | incident-svc | `INCIDENT_STORE_BACKEND=postgres` |
| postmortems-svc | `incident.postmortems` | postmortem-svc (shares incident schema) | `POSTMORTEM_STORE_BACKEND=postgres` |
| promotion-svc | `promotion.approval_decisions`, `promotion.deployment_plans`, `promotion.deployment_plan_extensions` | promotion-svc | `PROMOTION_STORE_BACKEND=postgres` |
| reconciliation-drift-svc | `reconciliation_drift.drift_evaluations`, `reconciliation_drift.alert_handoffs` | reconciliation-drift-svc | `RECONCILIATION_DRIFT_STORE_BACKEND=postgres` |
| memory-svc | `memory.institutional_memory_entries` | memory-svc | `PANTHEON_MEMORY_STORE_BACKEND=postgres` |

---

## 2. Fail-Closed Guarantee

All wave-3 services call `require_persistence_posture("<service>")` at startup
(before routes are live). If `PANTHEON_PERSISTENCE_POSTURE` is `production` or
`staging-live` and a service-specific store backend is not set to `postgres`, the
process raises `RuntimeError` and refuses to start.

Verification command (substitute the service name):

```bash
PANTHEON_PERSISTENCE_POSTURE=production \
GOVERNANCE_STORE_BACKEND=json \
python -c "from services.foundation.persistence_posture import require_persistence_posture; require_persistence_posture('governance')"
# Expect: RuntimeError: governance store backend must be postgres in production posture
```

---

## 3. Env Vars Required in Staging/Prod

All of the following must be present in `env/prod-control.env.example` (and the
real env file) for staging/prod operation:

```
PANTHEON_PERSISTENCE_POSTURE=production
DATABASE_URL=postgresql://pantheon_app:<password>@postgres:5432/pantheon

# Wave 3 control-plane backends
GOVERNANCE_STORE_BACKEND=postgres
GOVERNANCE_AUDIT_BACKEND=postgres
CAPITAL_STORE_BACKEND=postgres
CAPITAL_AUDIT_BACKEND=postgres
INCIDENT_STORE_BACKEND=postgres
POSTMORTEM_STORE_BACKEND=postgres
PROMOTION_STORE_BACKEND=postgres
PANTHEON_MEMORY_STORE_BACKEND=postgres
RECONCILIATION_DRIFT_STORE_BACKEND=postgres
```

Optional per-service DSN overrides for stricter role separation:

```
GOVERNANCE_STORE_DSN=postgresql://governance_app:<pw>@postgres:5432/pantheon
CAPITAL_STORE_DSN=postgresql://capital_app:<pw>@postgres:5432/pantheon
INCIDENT_STORE_DSN=postgresql://incident_app:<pw>@postgres:5432/pantheon
POSTMORTEM_STORE_DSN=...
PROMOTION_STORE_DSN=...
PANTHEON_MEMORY_STORE_DSN=...
RECONCILIATION_DRIFT_STORE_DSN=...
```

If a per-service DSN is not set, services fall back to `DATABASE_URL`.

---

## 4. Schema Bootstrap

Wave-3 services use `PostgresJsonOwnerStore.bootstrap()` to auto-create schemas
and tables on first connection. No manual SQL migration is required before
startup. Each service creates:

```sql
CREATE SCHEMA IF NOT EXISTS <schema>;
CREATE TABLE IF NOT EXISTS <schema>.<table> (
    record_id TEXT PRIMARY KEY,
    payload   JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Bootstrap is idempotent — safe to run against an existing cluster.

### Manual pre-check (optional)

To verify that the `pantheon_app` role has the necessary grants before starting:

```bash
psql "${DATABASE_URL}" -c "\dn"   # list schemas
psql "${DATABASE_URL}" -c "\dp governance.*"   # check table privileges
```

If tables are absent, they will be created automatically on first service start.

---

## 5. Dev Rollback

To roll a staging or control-plane compose back to JSON/JSONL:

```bash
export PANTHEON_PERSISTENCE_POSTURE=dev
export GOVERNANCE_STORE_BACKEND=json
export GOVERNANCE_AUDIT_BACKEND=jsonl
export CAPITAL_STORE_BACKEND=json
export CAPITAL_AUDIT_BACKEND=jsonl
export INCIDENT_STORE_BACKEND=json
export POSTMORTEM_STORE_BACKEND=json
export PROMOTION_STORE_BACKEND=json
export PANTHEON_MEMORY_STORE_BACKEND=json
export RECONCILIATION_DRIFT_STORE_BACKEND=json
```

Local data volumes are preserved in the compose named volumes, so rollback does
not destroy existing JSON files.

---

## 6. Compose Topology

### docker-compose.control.yml (VM-1 control-plane slice)

`reconciliation-drift-svc` was added in this wave. It depends on `telemetry`,
`lineage-read`, and `postgres` (all on VM-1). The VM-2 runtime-manager is
accessed via the `PANTHEON_RUNTIME_MANAGER_URL` env var — it is not co-located
on VM-1.

### docker-compose.yml (full dev compose)

All wave-3 services are present with `${SERVICE_STORE_BACKEND:-json}` defaults,
allowing dev environments to run without Postgres backend configuration.

---

## 7. Verification Commands

```bash
# 1. Confirm all wave 3 env vars are set in prod-control.env.example
grep -E 'GOVERNANCE_STORE_BACKEND|CAPITAL_STORE_BACKEND|INCIDENT_STORE_BACKEND|POSTMORTEM_STORE_BACKEND|PROMOTION_STORE_BACKEND|PANTHEON_MEMORY_STORE_BACKEND|RECONCILIATION_DRIFT_STORE_BACKEND' env/prod-control.env.example

# 2. Run focused owner store tests
python -m pytest services/foundation/tests/test_control_plane_postgres_owner_stores.py -v

# 3. Run persistence posture tests
python -m pytest services/foundation/tests/test_persistence_posture.py -v

# 4. Confirm reconciliation-drift-svc present in control compose
grep -A 5 'reconciliation-drift-svc:' docker-compose.control.yml

# 5. Dry-run docker compose config validation (requires docker compose CLI)
docker compose -f docker-compose.control.yml config --quiet
```

---

## 8. Wave Context

| Wave | Task ID | Services |
|---|---|---|
| Wave 2 | SVC-POSTGRES-PRODUCTION-OWNERSHIP-WAVE2 | consultation, source-ingest, search, training-session, policy-learning, research-orchestrator, research-worker-gateway |
| Wave 3 | SVC-BLUEPRINT-POSTGRES-CUTOFF-WAVE3 (this) | governance, capital, incidents, postmortems, promotion, reconciliation-drift, memory |

Wave 2 services are covered by the `PANTHEON_PERSISTENCE_POSTURE` + `PANTHEON_SOURCE_SEARCH_POSTURE` gates in `env/prod-control.env.example` and `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` section 4.1.
