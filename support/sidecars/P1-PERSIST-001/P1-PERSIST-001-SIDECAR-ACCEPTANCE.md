# P1-PERSIST-001 Sidecar Acceptance Packet

**Task ID:** P1-PERSIST-001-SIDECAR-ACCEPTANCE
**Parent Task:** P1-PERSIST-001 — Staging/prod Postgres and object store posture guard
**Owner:** Claude2
**Reviewer:** Codex
**Status:** Review approved — corrections incorporated 2026-05-01
**Prepared:** 2026-05-01
**Branch:** backend-dev-publish-20260429

---

## 1. Task Overview

This sidecar prepares the acceptance checklist, dependency map, and implementation
guide for the parent task `P1-PERSIST-001`.

`P1-PERSIST-001` delivers the **staging/prod Postgres and object store posture guard**
for the Pantheon platform. The goal is to ensure that:

- All services fail fast on startup when deployed to staging or production without
  a Postgres DSN and object-store credentials.
- JSON/JSONL fallback stores are explicitly dev-only — they must not silently operate
  in staging/prod.
- The environment persistence posture is visible in the service health and runtime
  metadata endpoints so operators can see what store backend each service is using
  and whether it meets production requirements.

This packet maps each acceptance criterion to the current codebase state, identifies
gaps, and outlines the required deliverables for the parent task owner (Codex).

This is a `support_only` sidecar. It does not modify any canonical docs, runtime
code, governance contracts, or L1 policy files.

---

## 2. Acceptance Checklist

Parent acceptance criteria from `ai-status.json`:

1. **AC-1** — staging/prod fail fast without Postgres/object store
2. **AC-2** — dev fallback clearly dev-only
3. **AC-3** — environment posture visible in health/runtime metadata

---

### AC-1 — Staging/prod fail fast without Postgres/object store

#### AC-1.1 — Source-ingest and search (existing partial coverage)

`services/source_search_posture.py` already enforces Postgres + object-store in
`PANTHEON_SOURCE_SEARCH_POSTURE ∈ {staging, prod, production}`:

| Check | Guard Implemented | File |
|---|---|---|
| `DATABASE_URL` must be a postgres DSN | Yes | `source_search_posture.py:81` |
| `SOURCE_INGEST_EVIDENCE_BACKEND` must be `postgres` | Yes | `source_search_posture.py:84` |
| `SEARCH_INDEX_STORE_BACKEND` + `SEARCH_EVIDENCE_BACKEND` must be `postgres` | Yes | `source_search_posture.py:84` |
| `SEARCH_DURABLE_INDEX_ONLY` must be true | Yes | `source_search_posture.py:86` |
| Object-store env vars (`PANTHEON_S3_*`, `PANTHEON_ARTIFACT_BUCKET`) required | Yes | `source_search_posture.py:88–90` |

`require_source_search_posture()` raises `RuntimeError` on violation.

**Verdict: AC-1 PARTIAL for source-ingest/search (covered). All other services NOT YET COVERED.**

#### AC-1.2 — Governance / capital / incident / promotion / consultation services

These services have `pg_store` implementations in `services/foundation/postgres_json_store.py`.
The `PostgresJsonOwnerStore.__init__` raises `ValueError` if `dsn=""`, so instantiation
fails fast — but only when the caller attempts to instantiate with an empty DSN.

Current state: services typically select the backend via env var and instantiate a
Postgres store only when the env var is set. If the env var is absent in staging/prod,
the service silently falls back to the in-memory or JSON store.

| Service | pg_store file | Staging/prod fail-fast guard | Gap |
|---|---|---|---|
| governance-svc | `services/governance/pg_store.py` | No env guard | Must fail fast if staging/prod and DSN absent |
| capital-pool-svc | `services/capital/pg_store.py` | No env guard | Must fail fast if staging/prod and DSN absent |
| incident-svc | `services/incident/pg_store.py` | No env guard | Must fail fast if staging/prod and DSN absent |
| promotion-svc | `services/promotion/pg_store.py` | No env guard | Must fail fast if staging/prod and DSN absent |
| consultation-svc | `services/consultation/store.py` | No env guard | Must fail fast if staging/prod and DSN absent |
| training-session-svc | `services/training-session/store.py` | Env-opt-in only | Must fail fast if staging/prod and backend=jsonl |
| research-orchestrator | `services/research/store.py` | No env guard | Must fail fast if staging/prod and DSN absent |
| research-worker-gateway | `services/research-worker-gateway/store.py` | No env guard | Must fail fast if staging/prod and DSN absent |
| policy-learning-svc | `services/policy-learning/store.py` | No env guard | Must fail fast if staging/prod and DSN absent |
| search-svc | `services/search/pg_store.py` | Covered by source_search_posture | Covered by AC-1.1 |
| source-ingest-svc | `services/source_ingestion/pg_store.py` | Covered by source_search_posture | Covered by AC-1.1 |

#### AC-1.3 — reconciliation-drift service

`services/reconciliation-drift/store.py` already provides `PostgresReconciliationDriftStore`
and `build_reconciliation_drift_store()` which accepts `RECONCILIATION_DRIFT_STORE_BACKEND=postgres`.
The Postgres option exists.

The remaining gap is that `build_reconciliation_drift_store()` does not enforce fail-fast
in staging/prod when `RECONCILIATION_DRIFT_STORE_BACKEND` is absent or set to `json`.

| Check | Status |
|---|---|
| Postgres-backed reconciliation drift store | Implemented (`PostgresReconciliationDriftStore`) |
| Fail-fast guard in staging/prod when backend=json | Not yet enforced |
| Posture surfaced in health dependencies | Not yet wired |

Codex must: add a startup fail-fast check via `require_persistence_posture()` (or equivalent)
when `PANTHEON_ENV=staging/prod` and the backend is not `postgres`, and wire posture into
the health endpoint.

#### AC-1.4 — Object store posture guard (beyond source-search)

`PANTHEON_ARTIFACT_BUCKET` and `PANTHEON_S3_*` env vars are currently only validated
in `source_search_posture.py`. Artifact-loader, evaluation, and registry artifact
storage may also reference an object store without a posture guard.

| File | Object store usage | Posture guard |
|---|---|---|
| `services/source_search_posture.py` | PANTHEON_S3_* + PANTHEON_ARTIFACT_BUCKET | Yes (enforced in staging/prod) |
| `services/execution/artifact_loader.py` | Artifact store access | No posture guard visible |
| `services/registry/models.py` | storage_ref field | No startup guard |

Codex should confirm whether artifact-loader and registry use the same object-store
env vars and whether they require a separate posture check or are covered indirectly
by source_search_posture.

**Verdict AC-1: PARTIAL — source/search covered; governance/capital/incident/promotion/
consultation/training-session/research/reconciliation-drift not yet guarded.**

---

### AC-2 — Dev fallback clearly dev-only

"Dev-only" means: when `PANTHEON_ENV` (or an equivalent posture env var) is
`staging` / `prod` / `production`, any service that falls back to JSON/JSONL
should fail fast with a clear error instead of silently degrading.

#### Current state of dev fallback labelling

| Service | Default backend | Env var that promotes to Postgres | Guard on JSON fallback in staging/prod |
|---|---|---|---|
| training-session-svc | JSONL | `TRAINING_SESSION_EVENT_STORE_BACKEND=postgres` | No guard |
| research-orchestrator | JSONL | `RESEARCH_EVENT_STORE_BACKEND=postgres` (from pilot doc) | No guard |
| policy-learning-svc | embedded JSON | Similar opt-in | No guard |
| research-worker-gateway | JSONL | Similar opt-in | No guard |
| reconciliation-drift | flat JSON | None (no Postgres option) | N/A |
| registry-svc | in-memory | None — in-memory store | Must be dev-only |
| governance (approval_decision, deployment_plan) | in-memory or JSON | pg_store requires explicit DSN | No guard |

**Gaps:**

1. No unified env var like `PANTHEON_ENV` or `PANTHEON_PERSISTENCE_POSTURE` that
   all services can read to enforce Postgres in staging/prod.
2. `source_search_posture.py` uses `PANTHEON_SOURCE_SEARCH_POSTURE` — useful pattern
   but scoped only to source/search.
3. A `services/foundation/persistence_posture.py` module (extending the pattern from
   `source_search_posture.py`) would give all services a consistent way to: check the
   environment tier, refuse JSON/JSONL in staging/prod, and surface errors in health.

**Verdict AC-2: NOT MET — generalized "dev-only" label and guard pattern does not exist.**

---

### AC-3 — Environment posture visible in health/runtime metadata

`services/foundation/health.py` provides `health_payload()` which accepts a
`dependencies` dict. If a dependency has `status: error`, the overall service health
degrades to `degraded` and `/readyz` returns 503.

**Current state:** Source-ingest and search already call `require_source_search_posture()`
at startup and wire `source_search_posture` into `health_payload(dependencies=...)` in
`services/source_ingestion/main.py` and `services/search/main.py`. This existing coverage
must be preserved and must not be duplicated.

All other pg_store services do not yet wire persistence posture into health dependencies.

| Health surface | Postgres posture surfaced | Gap |
|---|---|---|
| `services/foundation/health.py` | Framework only — no posture wiring | Each service must add posture to dependencies |
| source-ingest `/healthz` | **Yes** — `source_search_posture` wired | Covered; preserve existing path |
| search `/healthz` | **Yes** — `source_search_posture` wired | Covered; preserve existing path |
| governance/capital/incident | Not wired | Must add `persistence_posture` dependency |
| reconciliation-drift | Not wired | Must add posture wiring alongside Postgres enforcement |
| BFF `/healthz` | Includes downstream dependencies | Would reflect them if services surface posture |

**Required pattern for services without existing posture wiring:**

Each service that has a pg_store should call `validate_*_posture()` (or a new
`validate_persistence_posture()`) and include the result as a named dependency in
`health_payload()`:

```python
posture = validate_persistence_posture(service="governance-svc")
health_payload(
    "governance-svc",
    dependencies={"persistence": {"status": posture.status, "mode": posture.mode}},
)
```

**Verdict AC-3: PARTIAL — source-ingest and search already surface source-search posture
in health dependencies. All non-source/search services with a pg_store do not yet surface
generalized persistence posture.**

---

## 3. Dependency Map

### 3.1 Upstream dependencies for P1-PERSIST-001

| Dependency | Status | Notes |
|---|---|---|
| `P0-CI-BOUNDED-001` — Add source/search bounded and fail-closed adapter CI | `done` | Established `source_search_posture.py` + CI pattern that P1-PERSIST-001 extends |

### 3.2 Related canonical policy files (read before implementing)

| File | Relevance |
|---|---|
| `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` | Per-service write-owner rule; shared cluster allowed; read-only role required for cross-service |
| `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md` | Storage-layer policy for telemetry and lineage |
| `PAPER_CANARY_LIVE_POLICY.md` | Deployment-stage policy — paper vs staging/prod posture |
| `TARGET_ARCHITECTURE.md` | Platform-level storage-tier expectations |
| `SA-13_contract_schema_gap_analysis.md` (§12) | Storage contract gap analysis (Registry Store / Runtime Store / Telemetry Store / Evidence Store / Incident Store / Audit Store) |
| `SA-20_v2_risk_register_corrected.md` (Risk #11) | Data persistence env-gated rollout gap: medium severity |

### 3.3 Downstream tasks depending on P1-PERSIST-001

No tasks in the current sprint have `P1-PERSIST-001` as an explicit `depends_on`.
However, P1-PERSIST-001 is a **prerequisite** for any production activation path:
- `P1-LIVE-PLAN-001` (canary/live activation criteria) implicitly requires staging
  posture guards before live gate opens.
- Any future activation of governance, capital, or incident services in staging/prod
  requires the fail-fast guard to be in place.

---

## 4. Required Deliverables for Parent Task

When Codex implements P1-PERSIST-001, the following artifacts must exist:

### 4.1 Foundation persistence posture module

| File | Purpose |
|---|---|
| `services/foundation/persistence_posture.py` | Generalized posture check for all services: validates DATABASE_URL, object-store env vars, and per-service backend selection in staging/prod; mirrors pattern from `source_search_posture.py` |

This module should:
- Accept a `service` name and optionally a backend env var to inspect.
- Read a tier env var (recommended: `PANTHEON_ENV` or reuse `PANTHEON_PERSISTENCE_POSTURE`) to determine enforcement tier.
- In `{staging, prod, production}`: raise `RuntimeError` (or return error PostureCheck) if DATABASE_URL is absent or not a Postgres DSN, or if any service-specific backend is not `postgres`.
- Return a `PostureCheck`-compatible object that can be included in `health_payload(dependencies=...)`.

### 4.2 Service startup posture guards

Each service that uses a pg_store must call `require_persistence_posture()` at startup
(before accepting traffic). The following services require this:

| Service | Startup file | Required change |
|---|---|---|
| governance-svc | `services/governance/` main | Add `require_persistence_posture("governance-svc")` |
| capital-pool-svc | `services/capital/` main | Add posture require |
| incident-svc | `services/incident/` main | Add posture require |
| promotion-svc | `services/promotion/` main | Add posture require |
| consultation-svc | `services/consultation/` main | Add posture require |
| training-session-svc | `services/training-session/` main | Add posture require (fail if staging/prod + jsonl backend) |
| research-orchestrator | `services/research/main.py` | Add posture require |
| research-worker-gateway | `services/research-worker-gateway/main.py` | Add posture require |
| policy-learning-svc | `services/policy-learning/main.py` | Add posture require |

### 4.3 reconciliation-drift staging/prod fail-fast and posture wiring

`PostgresReconciliationDriftStore` already exists. The remaining work is:

- In `build_reconciliation_drift_store()` (or the service startup), add a fail-fast
  check when `PANTHEON_ENV=staging/prod` and backend is not `postgres`. Example:
  ```python
  if env in {"staging", "prod", "production"} and backend != "postgres":
      raise RuntimeError(
          "reconciliation-drift requires RECONCILIATION_DRIFT_STORE_BACKEND=postgres "
          "in staging/prod; JSON fallback is dev-only."
      )
  ```
- Wire the posture result into the health endpoint using the same pattern as other services.

### 4.4 Health endpoint wiring for posture

Each service startup must call:

```python
posture = validate_persistence_posture(service="<svc-name>")
# Wire into health_payload as a named dependency
```

Existing health routes using `services/foundation/health.py` must include
`persistence` in the `dependencies` dict.

### 4.5 CI posture guard tests

| File | Purpose |
|---|---|
| `scripts/check_platform_persistence_posture.py` | Assert staging/prod posture across all services (DATABASE_URL present and valid, object-store env vars present, no JSON backends in staging/prod) |
| `services/foundation/tests/test_persistence_posture.py` | Unit tests for `persistence_posture.py` |
| `.github/workflows/p1-persist-posture.yml` (or extend existing) | CI job `ci-persist-posture` that runs the posture check script |

---

## 5. Hard Invariants to Be Covered

| Invariant | Coverage |
|---|---|
| INV-PERSIST-001 — In `{staging, prod, production}`, all service stores must use Postgres, not JSON/JSONL | `persistence_posture.py` + startup require |
| INV-PERSIST-002 — In `{staging, prod, production}`, `DATABASE_URL` must be a valid `postgresql://` or `postgres://` DSN | Validated in posture module |
| INV-PERSIST-003 — In `{staging, prod, production}`, object-store env vars (`PANTHEON_S3_ENDPOINT`, `PANTHEON_ARTIFACT_BUCKET`, `PANTHEON_S3_ACCESS_KEY`, `PANTHEON_S3_SECRET_KEY`) must all be non-empty | Validated in posture module |
| INV-PERSIST-004 — JSON/JSONL fallback stores must not be reachable in staging/prod (fail-fast before accepting traffic, not at first write) | Startup `require_persistence_posture()` call |
| INV-PERSIST-005 — Each service health endpoint must surface `persistence` status in `dependencies` | Health wiring |
| INV-PERSIST-006 — `dev` tier is the only tier where JSON/JSONL fallbacks are allowed | Posture module enforcement scope |

---

## 6. Existing Coverage (Do Not Re-implement)

The following already satisfy part of the above requirements and should be extended,
not replaced:

| Existing artifact | What it covers | Action for Codex |
|---|---|---|
| `services/source_search_posture.py` | source-ingest + search posture | Reuse pattern; do not duplicate; extend coverage |
| `services/foundation/postgres_json_store.py` | Raises ValueError on empty DSN | Remains as the base store guard; startup require is additive |
| `services/foundation/health.py` | `health_payload()` with `dependencies` | Wire posture check into each service's `dependencies` dict |
| `services/source_ingestion/main.py` | Calls `require_source_search_posture()` at startup and wires `source_search_posture` into health | **Do not duplicate**; preserve existing coverage |
| `services/search/main.py` | Calls `require_source_search_posture()` at startup and wires `source_search_posture` into health | **Do not duplicate**; preserve existing coverage |
| `services/source_ingestion/pg_store.py` | Source-ingest Postgres store | Already implemented; posture startup require exists via source_search_posture |
| `services/reconciliation-drift/store.py` | `PostgresReconciliationDriftStore` and `build_reconciliation_drift_store()` | pg_store exists; needs staging/prod fail-fast and health wiring |
| `services/capital/pg_store.py`, `services/governance/pg_store.py`, etc. | Per-service pg_stores | Already implemented; need posture startup require |

---

## 7. Sidecar Self-Checklist

- [x] Support artifacts created only.
- [x] Canonical truth (L1 docs, runtime code, `ai-status.json`, core contracts) has NOT been edited by this sidecar.
- [x] Acceptance checklist maps each AC to current code state and identifies gaps.
- [x] Dependency map covers upstream and downstream dependencies.
- [x] Required deliverables listed with file-level specificity.
- [x] Hard invariants defined for CI verification.
- [x] Acceptance packet handed off to assigned reviewer (Codex).
- [x] Codex review non-blocking corrections incorporated (2026-05-01):
  - Corrected AC-1.3: `reconciliation-drift` has Postgres option; gap is fail-fast and posture wiring only.
  - Corrected AC-3: source-ingest/search already wire posture into health; verdicts updated accordingly.
  - Added existing coverage entries for `reconciliation-drift` pg store and source/search health wiring.

---

## 8. Open Items / Notes for Reviewer

1. **Unified tier env var:** `source_search_posture.py` uses `PANTHEON_SOURCE_SEARCH_POSTURE`.
   The new `persistence_posture.py` module should use the same env var
   (`PANTHEON_PERSISTENCE_POSTURE`) or a new `PANTHEON_ENV` that all services share.
   Codex should align with the L1 policy in `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md`
   before choosing the env var name.

2. **reconciliation-drift gap:** This service has no Postgres option. If Codex decides
   to leave reconciliation-drift as dev-only for now, the startup must still fail fast
   in staging/prod (e.g., `if env == "staging": raise RuntimeError("reconciliation-drift
   requires Postgres backend; dev-only mode not allowed in staging/prod")`).

3. **registry service in-memory store:** `services/registry/storage.py` uses an
   in-memory `RegistryStore` with no Postgres option. Registry is a core governance
   object store. Codex should flag this as out-of-scope for P1-PERSIST-001 (covered
   by a later task) or add a dev-only guard if registry is deployed in staging/prod
   without a Postgres store.

4. **BFF read_store persistence:** `services/control-plane/bff/read_store.py` may
   cache derived state. Codex should verify whether BFF has its own persistence
   (e.g., for staging/prod cutoff contracts) or is purely a projection layer.

5. **Object store scope:** The object-store posture guard in `source_search_posture.py`
   covers evidence/artifact buckets. If `artifact_loader.py` uses a separate bucket
   configuration, Codex should confirm whether the same env vars apply.

6. **Test isolation:** The posture unit tests in `test_persistence_posture.py` must
   not require a live Postgres or S3 connection. Use `unittest.mock.patch.dict(os.environ, ...)`
   as in `test_source_search_posture.py` pattern.
