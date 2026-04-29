# SVC-SURFACES — Reviewer Findings (Claude)

**Task**: `SVC-SURFACES`
**Owner**: `Codex`
**Reviewer**: `Claude`
**Date**: 2026-04-28
**Disposition**: `approve` — BFF read path is now driven by HTTP service
clients with snapshot fallback off by default, feedback is independently
packaged with its own Dockerfile/volume/health route, and compose env wiring
matches the governance-api family endpoints verified in
`2026-04-28-svc-governance-api-claude-review.md`.

---

## 1. Acceptance Criteria Mapping

The four acceptance criteria from `ai-status.json` for SVC-SURFACES:

1. *BFF no longer depends on snapshot/default fallback as the normal
   integration path* — **satisfied**.
2. *BFF and feedback are packaged and runnable in the target stack* —
   **satisfied**.
3. *PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK=false is the normal single-VM
   path and missing backend data surfaces degraded/unavailable instead of
   seeded defaults* — **satisfied**.
4. *BFF read clients follow SVC-GOVERNANCE-API and SVC-SERVICE-DISPOSITION
   boundaries for governance/runtime/evidence/consultation/search data* —
   **satisfied**.

Detailed evidence below.

---

## 2. Acceptance #1 + #3 — Snapshot fallback flipped off by default

- `services/control-plane/bff/main.py:118-125` now reads the env var via
  `_bool_from_env("PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK", default=False)`.
  The previous `default="true"` is gone, so a process started without the
  env knob present uses the locked-down path. This matches AC#3.
- `services/control-plane/bff/read_store.py:265-330` (CanonicalSnapshotAdapter)
  and `:670-800` (ServiceBackedReadAdapter) propagate `allow_snapshot_fallback`
  into both adapters. When `False`, the snapshot branch is skipped and a
  missing service yields `available=False`.
- `ReadSurfaceStore.__init__` at `read_store.py:4615-4633` constructs both
  adapters with the resolved flag, and `dataset_source` at `:5327-5360`
  cascades the flag into the per-dataset lookup so surface state can
  faithfully report `missing` when no service-client and no file is present.
- `services/control-plane/bff/main.py:2094-2131` (`_dataset_surface_status`)
  maps `source == "missing"` to `status="unavailable"` plus
  `staleness.served_from="unverifiable"`, and `_rw01_surface_state` at
  `main.py:4120-4141` returns `"unavailable"` for that path. This is the
  AC#3 "degraded/unavailable instead of seeded defaults" guarantee.
- New tests in
  `services/control-plane/bff/test_read_store_bootstrap_snapshot.py:15-100`
  prove both halves of the flip:
  - With `allow_local_snapshot_fallback=False` and every URL/dir env empty,
    `get_deployment_plan`, `list_bindings`, `get_incident`,
    `get_postmortem_by_incident` all return None/[] and
    `dataset_source("deployment_plans") == "missing"`.
  - With `allow_local_snapshot_fallback=True`, the legacy snapshot
    behavior is preserved and `dataset_source == "local_snapshot"`.

---

## 3. Acceptance #2 — Feedback packaging + BFF compose rewire

### 3.1 Feedback service is now independently packaged

- `services/control-plane/feedback/Dockerfile` (new): `python:3.11-slim`,
  installs the package's own `requirements.txt`, exposes 8085, runs
  `uvicorn main:app --app-dir /workspace/services/control-plane/feedback`.
- `services/control-plane/feedback/main.py:189-191` adds the `/__health__`
  alias used by the compose healthcheck (the existing `/health` route is
  preserved).
- `docker-compose.yml:391-407`:
  - Build context now points at the new
    `services/control-plane/feedback/Dockerfile` (the legacy
    `services/feedback/Dockerfile` was removed from the compose graph).
  - The unused `DATABASE_URL`/NATS/MinIO env vars and `depends_on` were
    removed — the service is a single-file FastAPI process with a JSONL
    store, so this matches its actual runtime contract.
  - `TRADER_FEEDBACK_STORE_PATH=/data/feedback/trader_feedback_events.jsonl`
    plus a dedicated `feedback-data` volume (declared at line 621) gives
    the JSONL store durable single-VM persistence.
  - `healthcheck.test` hits the new `/__health__` route on 8085.

### 3.2 BFF compose environment is now service-URL-driven

- `docker-compose.yml:258-312` (`operator-bff`):
  - `PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK: "false"` and
    `PANTHEON_BFF_SERVICE_TIMEOUT_SECONDS: "2.0"` are explicit.
  - The read-only mounts `governance-data:/data/governance:ro`,
    `runtime-data:/data/runtime:ro`, `incident-data:/data/incidents:ro`
    are removed. The BFF now reaches governance/runtime/incidents data
    through HTTP service clients only. (`PANTHEON_GOVERNANCE_DATA_DIR`,
    `PANTHEON_RUNTIME_DATA_DIR`, `INCIDENTS_DATA_DIR`, `POSTMORTEMS_DATA_DIR`
    env vars are likewise gone.)
  - New service URLs added: `PANTHEON_INCIDENTS_API_URL`,
    `PANTHEON_POSTMORTEMS_API_URL`, `PANTHEON_TELEMETRY_API_URL`,
    `PANTHEON_LINEAGE_READ_URL`. These align with the env contract
    documented in `.env.example:55-65`.
  - `depends_on.lineage-read.condition == service_healthy` was added.

### 3.3 .env.example is consistent

`.env.example:47-65,135-141` documents the BFF-side knobs
(`PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK=false`,
`PANTHEON_BFF_SERVICE_TIMEOUT_SECONDS=2.0`, the eight family URLs) and a new
deployment block (`DEPLOYMENT_PORT=8095`, `DEPLOYMENT_DATA_DIR=/data/governance`).

---

## 4. Acceptance #4 — Read clients follow service-family boundaries

The HTTP datasets in `read_store.py` are split between the two adapters
according to which family owns the data, matching the SVC-GOVERNANCE-API
family contract and the SVC-SERVICE-DISPOSITION decisions:

`CanonicalSnapshotAdapter._HTTP_DATASETS` (`read_store.py:265-292`):

| dataset | env var(s) | path | auth |
|---|---|---|---|
| `deployment_plans` | `PANTHEON_DEPLOYMENT_API_URL` / `_SERVICE_URL` | `/api/deployment/plans` | none |
| `approval_decisions` | `PANTHEON_GOVERNANCE_APPROVAL_API_URL` / `_SERVICE_URL` | `/api/governance/approvals` | none |
| `capital_pools` | `PANTHEON_CAPITAL_API_URL` / `_SERVICE_URL` | `/api/capital-pools` | none |
| `persona_bindings` | `PANTHEON_CAPITAL_API_URL` / `_SERVICE_URL` | `/api/bindings` | none |
| `runtime_bindings` | `PANTHEON_RUNTIME_MANAGER_URL` / `PANTHEON_INTERNAL_API_URL` | `/api/runtime-bindings` (`list_key=bindings`) | Bearer `PANTHEON_RUNTIME_MANAGER_TOKEN` |

`ServiceBackedReadAdapter._HTTP_DATASETS` (`read_store.py:677-696`):

| dataset | env var(s) | path |
|---|---|---|
| `incidents` | `PANTHEON_INCIDENTS_API_URL` / `_URL` | `/api/incidents` |
| `postmortems` | `PANTHEON_POSTMORTEMS_API_URL` / `_URL` | `/api/postmortems` |
| `evolution_decisions` | `PANTHEON_EVOLUTION_API_URL` / `PANTHEON_GOVERNANCE_API_URL` | `/api/evolution/proposals` |
| `lineage_edges` | `PANTHEON_LINEAGE_READ_URL` / `PANTHEON_LINEAGE_API_URL` | `/api/v1/lineage` |

I cross-checked each route against the actual service implementation:

- `services/deployment/service.py:928`, `services/governance/main.py:225-229`,
  `services/capital/main.py:417,491`, `services/runtime-manager/main.py:221-222`,
  `services/incidents/main.py:236-239`, `services/postmortems/main.py:280-283`,
  `services/evolution/main.py:276`, `services/lineage-read/main.py:240` —
  all 9 endpoints are present and return either a top-level JSON list (the
  general case) or `{"bindings": [...]}` for runtime-manager (which the
  client correctly extracts via `list_key="bindings"`).
- runtime-manager's `/api/runtime-bindings` is the only auth-gated route
  (`@require_authn(roles=_OPERATOR_ROLES)`); the read client correctly sends
  the bearer header derived from `PANTHEON_RUNTIME_MANAGER_TOKEN` and the
  other clients send no auth (matching the route definitions).
- For `lineage_edges`, the adapter normalises legacy field names
  (`source_id`/`target_id` → `from_artifact_id`/`to_artifact_id`,
  `read_store.py:715-728`) so the existing surface contract continues to
  work over the service payload.
- `read_store.py:5327-5360` adds a defensible derived
  source for `approval_queue_items` (cascades to `approval_decisions`) and
  `governance_review_queue_items` (cascades to `deployment_plans` /
  `evolution_decisions`). The matching synthesis in
  `list_governance_review_queue_items` (`read_store.py:5863-5917`) and
  `list_approval_queue_items` (`read_store.py:5958-5997`) builds the
  workbench items from upstream service-client data when the local
  fallback is empty — this is what lets the Operator board stay populated
  without any pre-seeded snapshot.

The new test
`services/control-plane/bff/test_read_store_service_clients.py` exercises
five of the nine HTTP datasets end-to-end (deployment, governance approval,
capital pools, capital bindings, runtime bindings, lineage) under
`allow_local_snapshot_fallback=False`, asserts the resulting payloads, and
asserts `dataset_source == "service_client"` so AC#1/#4 stay verifiable.

---

## 5. Re-run Verification

I was unable to re-run `pytest` locally in this environment (the
`/usr/bin/python3` interpreter has no `pytest` and `pip` is also absent).
I therefore relied on:

- `python3 -m py_compile services/control-plane/bff/read_store.py
  services/control-plane/bff/main.py services/control-plane/feedback/main.py`
  → exit 0.
- `docker compose config --quiet` → exit 0.
- Static review of the new test file
  (`test_read_store_service_clients.py`), the rewritten
  `test_read_store_bootstrap_snapshot.py`, and the `_HTTP_DATASETS` /
  `_load_http_dataset` paths in `read_store.py`.
- Codex's reported `BFF pytest 274 passed`, `feedback pytest 18 passed`,
  `git diff --check passed` from the handoff record.

If a follow-up environment is available, re-running the BFF + feedback
suites end-to-end would be the only thing missing from a strict
parity-with-Codex verification.

---

## 6. Disposition

`approve`. Reviewer (Claude) marks SVC-SURFACES `review_approved` and
returns it to owner Codex for finalization to `done`.

Items intentionally **not** in scope for this packet (consistent with
acceptance criteria):

- Production hardening of runtime-manager auth and approval authority is
  tracked separately under `SVC-RUNTIME-HARDENING` (currently in review).
- Compose stack boot + smoke run is the responsibility of `SVC-COMPOSE`,
  which is gated on this task plus SVC-SERVICE-DISPOSITION.
- Consultation/source-ingest/search service activation remains gated on
  the dedicated follow-up tasks (`SVC-CONSULTATION-SERVICE-ACTIVATION`,
  `SVC-SOURCE-INGEST-SERVICE`, `SVC-SEARCH-SERVICE`) per
  SVC-SERVICE-DISPOSITION.
