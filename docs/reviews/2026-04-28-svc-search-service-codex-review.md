# SVC-SEARCH-SERVICE Review - Codex

- Date: 2026-04-28
- Task: `SVC-SEARCH-SERVICE` - Wrap governed search as deployable search service
- Owner: Codex2
- Reviewer: Codex
- Verdict: APPROVED - return to owner for finalization

## Scope Reviewed

- `services/search/main.py`
- `services/search/Dockerfile`
- `services/search/requirements.txt`
- `services/search/tests/test_http_service.py`
- `services/search/tests/test_service_activation_contract.py`
- `services/search/tests/test_governed_search.py`
- `services/control-plane/bff/read_store.py`
- `services/control-plane/bff/test_search_service_client.py`
- `docker-compose.yml`
- `scripts/smoke_honest_stack.py`

## Acceptance Check

1. Search service exposes governed query, health, and snapshot replay APIs - pass.
   - `services/search/main.py:190` builds the FastAPI app with a JSONL `JsonlSearchIndexStore`.
   - `services/search/main.py:194` exposes `/health` and reports the store path plus snapshot count.
   - `services/search/main.py:204` exposes `/api/search/query`, converts request documents into governed evidence records, requires persona/workspace scope, applies `SearchGateway`, returns `index_adapter` metadata, and returns the appended `index_snapshot`.
   - `services/search/main.py:233` exposes `/api/search/snapshots/{request_id}` and reloads the JSONL store before replay.

2. Dockerfile, env storage contract, and compose healthcheck are present - pass.
   - `docker-compose.yml:126` declares `search-svc` with `services/search/Dockerfile`, `PORT=8098`, `SEARCH_DATA_DIR=/data/search`, `SEARCH_INDEX_STORE_PATH=/data/search/search-index.jsonl`, `search-data:/data/search`, host port `${SEARCH_PORT:-18098}:8098`, and a `/health` healthcheck.
   - `docker-compose.yml:780` declares the top-level `search-data` volume.
   - `services/search/Dockerfile` installs the service-local requirements, exposes 8098, and starts `uvicorn services.search.main:app`.

3. BFF RW-02 search uses explicit search service URL in the normal path - pass.
   - `services/control-plane/bff/read_store.py:8131` resolves `PANTHEON_SEARCH_API_URL` / `PANTHEON_SEARCH_SERVICE_URL`.
   - `services/control-plane/bff/read_store.py:8233` builds the governed search service payload with explicit persona/workspace/access/license context.
   - `services/control-plane/bff/read_store.py:8299` posts to `/api/search/query` when a search service URL is configured.
   - `services/control-plane/bff/read_store.py:8421` prefers service results before falling back to the in-process governed search path.
   - `docker-compose.yml:382` wires `PANTHEON_SEARCH_API_URL=http://search-svc:8098`, and `docker-compose.yml:394` makes `operator-bff` wait for `search-svc` health.

4. Tests prove citations, ACL filtering, replayed refs, and compose wiring - pass.
   - `services/search/tests/test_http_service.py` covers service-level ACL prefiltering, cited evidence refs, adapter metadata, JSONL replay, and missing persona/workspace rejection.
   - `services/search/tests/test_governed_search.py` covers library-level cited refs, OpenClaw scope requirements, no raw payload leakage, durable index replay, and index-adapter keyword boundaries.
   - `services/search/tests/test_service_activation_contract.py` parses compose and smoke wiring for `search-svc`, BFF env/dependency, and Dockerfile/requirements.
   - `services/control-plane/bff/test_search_service_client.py` verifies BFF RW-02 calls `http://search-svc:8098/api/search/query` and records governed refs from the service response.

## Verification Run

- `python3 -m pytest services/search/tests/test_governed_search.py services/search/tests/test_http_service.py services/search/tests/test_service_activation_contract.py services/control-plane/bff/test_search_service_client.py` - 12 passed.
- `python3 -m pytest services/search/tests services/control-plane/bff/test_rw02_search_contract.py` - 18 passed.
- `python3 -m pytest services/control-plane/bff/test_read_store_service_clients.py services/control-plane/bff/test_search_service_client.py` - 4 passed.
- `docker compose config --quiet` - passed.

## Verdict

Approved. The deployable `search-svc` wrapper, persistent search-ref replay, BFF explicit service path, compose health wiring, and smoke coverage satisfy the task acceptance criteria. No blocking findings.
