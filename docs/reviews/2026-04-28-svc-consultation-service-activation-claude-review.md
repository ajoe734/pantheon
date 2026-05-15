# SVC-CONSULTATION-SERVICE-ACTIVATION Review — Claude

- Date: 2026-04-28
- Task: `SVC-CONSULTATION-SERVICE-ACTIVATION` — Promote consultation-svc into compose and BFF service client path
- Owner: Codex
- Reviewer: Claude
- Verdict: **APPROVED — return to owner for finalization**

## Artifacts reviewed

- `docker-compose.yml` (root)
- `services/consultation/Dockerfile`
- `services/consultation/main.py`
- `services/consultation/models.py`
- `services/consultation/client.py`
- `services/consultation/smoke_test.py`
- `services/consultation/test_compose_activation.py`
- `services/control-plane/bff/read_store.py`
- `services/control-plane/bff/test_read_store_service_clients.py`
- `services/control_plane/internal_api.py`
- `services/control_plane/test_internal_api_incident.py`

## Acceptance criteria check

1. **Root compose runs consultation service with healthcheck, port env, and durable volume** — pass.
   - `consultation-svc` is declared at `docker-compose.yml:81` with build context `.`, dockerfile `services/consultation/Dockerfile`, `PORT=8096`, `CONSULTATION_DATA_DIR=/data/consultation`, durable volume `consultation-data:/data/consultation`, host port `${CONSULTATION_PORT:-18096}:8096`, and a healthcheck that resolves PORT from env and probes `/health`. Volume is registered in the top-level `volumes:` block.
   - Dockerfile sets `ENV PORT=8096`, exposes 8096, and uses `sh -c "uvicorn ... --port ${PORT:-8096}"` so PORT remains override-friendly at runtime.

2. **BFF consultation reads and writes use explicit consultation service URL client in normal path** — pass.
   - `services/consultation/client.py` provides `ConsultationServiceClient` keyed off `PANTHEON_CONSULTATION_API_URL` / `PANTHEON_CONSULTATION_SERVICE_URL`; methods cover `list_requests`, `get_request`, `create_request`, `cancel_request`, `list_memos/get_memo`, `list_transcripts`, `list_handoffs(_for_request)`, and `record_sponsor_decision`. HTTP errors are wrapped in `ConsultationClientError` carrying status code.
   - `read_store.ReadSurfaceStore` now caches a `ConsultationServiceClient` instance (`_consultation_client`) and prefers it for the four consultation read datasets and for `create_consult_request` / `cancel_consult_request` / `record_sponsor_decision`. Local store path remains as fallback only when the URL is not configured. `dataset_source` returns `consultation_service_client` when the HTTP path serves the read, and `consultation_service_store` for the legacy data-dir path; with `allow_local_snapshot_fallback=False` it surfaces `missing` rather than masking absent service data.
   - Compose wires `PANTHEON_CONSULTATION_API_URL=http://consultation-svc:8096` on `operator-bff` and adds `consultation-svc` to its `depends_on` with `service_healthy`.

3. **Runtime sponsor handoff uses the same service boundary or an explicitly accepted service API** — pass.
   - `services/control_plane/internal_api.py:_record_service_committee_sponsor_decision` checks `ConsultationServiceClient.configured()` first and routes through the HTTP API when set; on `404` it raises `KeyError` (mapped to `COMMITTEE_NOT_FOUND` 404), `409` → `ValueError` (`COMMITTEE_HANDOFF_UNAVAILABLE` 409), and any other `ConsultationClientError` returns `CONSULTATION_API_UNAVAILABLE` 503 — clean, explicit error semantics rather than a silent fallthrough.
   - Compose sets `PANTHEON_CONSULTATION_API_URL` on `runtime-manager` with `depends_on: { consultation-svc: service_healthy }`, and the test confirms the env-var-only contract by asserting `PANTHEON_RUNTIME_CONSULTATION_DATA_DIR` is **not** set on the runtime-manager service.
   - Service-side `POST /api/consult/committees/{committee_id}/sponsor-decision` in `services/consultation/main.py` is the explicit handoff API: validates decision/rationale, requires a published memo, stamps the consult metadata, persists a `ConsultGateHandoff` (status `sent`), emits a `gate_handoff_created` audit, and returns the `service_handoff` shape both BFF and runtime callers project from.

4. **Tests prove restart persistence, BFF compatibility, and compose config** — pass.
   - `services/consultation/test_compose_activation.py` parses `docker-compose.yml` and asserts: consultation-svc build/PORT/data-dir/volume/host-port/healthcheck, that operator-bff and runtime-manager get `PANTHEON_CONSULTATION_API_URL=http://consultation-svc:8096`, that runtime-manager does **not** get a runtime-side data-dir, that smoke-stack has `CONSULTATION_URL` and `consultation-svc` in its depends_on, and that the named volume exists. Run: `pytest services/consultation/test_compose_activation.py` → 1 passed.
   - `services/consultation/smoke_test.py` exercises the full lifecycle (request → submit → assign → evidence → transcript → memo submit/publish → handoff) plus the new `cancel_request` and `record_committee_sponsor_decision` endpoints, and re-instantiates `ConsultationStore(test_dir)` after each scenario to assert restart-replay of state, audit log, handoff refs, and outbox. Run: `pytest services/consultation/smoke_test.py` → 4 passed.
   - `services/control-plane/bff/test_read_store_service_clients.py::test_consultation_reads_and_writes_use_http_service_client_when_url_configured` patches `read_store.ConsultationServiceClient` with a fake and verifies create/list/cancel/record_sponsor_decision all flow through the HTTP client (no shared dir), and that `dataset_source("consult_requests") == "consultation_service_client"`. Run: `pytest services/control-plane/bff/test_read_store_service_clients.py` → 3 passed.
   - `services/control_plane/test_internal_api_incident.py::test_sponsor_decision_route_uses_consultation_http_client_when_configured` patches `internal_api.ConsultationServiceClient` and asserts the runtime route returns the HTTP-client-shaped `service_handoff` and forwards the four required arguments. Run: `pytest services/control_plane/test_internal_api_incident.py` → 16 passed.
   - Additional regression coverage from BFF surfaces (`http_smoke_test.py`, `test_evolution_center_contract.py`, `test_tw03_before_after_compare_contract.py`) → 9 passed under the same run, confirming nothing in the broader BFF read path regressed.

## Cross-document / cross-service consistency

- The runtime no longer reads `PANTHEON_RUNTIME_CONSULTATION_DATA_DIR` in the compose normal path; the explicit-fallback check in `test_root_compose_wires_consultation_service_boundary` asserts this contract.
- BFF compose still mounts `runtime-data` / `incident-data` read-only for the legacy datasets that haven't been service-promoted yet, but consultation no longer relies on a shared dir — matching the brief's "不再依賴隱性 shared data-dir" requirement.
- `smoke-stack` gets `CONSULTATION_URL=http://consultation-svc:8096` and `consultation-svc: service_healthy` as a dependency, so the in-stack smoke run will exercise the service over its API surface.

## Verdict

Approved. The consultation lifecycle is now a first-class deployable service in the root compose; both the BFF read/write surfaces and the runtime sponsor-handoff route consume it through `ConsultationServiceClient` against the explicit `PANTHEON_CONSULTATION_API_URL`, with deterministic error mapping when the service is unreachable. Restart-persistence, compose-config correctness, and BFF compatibility are all proven by focused tests. Returning to Codex for finalization to `done`.
