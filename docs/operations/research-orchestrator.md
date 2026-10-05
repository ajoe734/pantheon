# Research Orchestrator Single-Owner Runbook & Operating Architecture

Last updated: 2026-10-02
Status: Active operational specification
Task: `BFF-RESEARCH-SINGLE-OWNER-001`

## 1. Overview & Single Ownership Boundary

The Research service (`services/research/main.py`) is the sole authoritative owner for:
- Research execution dispatch (tasks, stages, runs).
- Research mutations (tickets, experiments, notes).
- Direct PostgreSQL write-owner interaction (`services/research/write_owner.py`).

The Backend-For-Frontend (BFF) layer (`services/control-plane/bff`):
- Does NOT instantiate `ResearchDispatcher` or `ResearchWriteOwner`.
- Does NOT perform direct SQL mutations on research tables.
- Validates caller authentication, workshop ownership, approval tokens, and tenant scoping.
- Proxies research commands via typed HTTP clients (`ResearchServiceClient` and research orchestrator HTTP ports).
- Projects read models and receipts back to UI and callers.

## 2. API Endpoints Owned by Research Service

### 2.1 Orchestrator Execution APIs
- `POST /api/research-orchestrator/tasks`
  - Submits or registers a research task.
- `GET /api/research-orchestrator/tasks/{task_id}`
  - Fetches task definition and stage metadata.
- `POST /api/research-orchestrator/tasks/{task_id}/runs`
  - Creates and records a research execution run.
- `GET /api/research-orchestrator/tasks/{task_id}/runs/{run_id}`
  - Returns run execution details and recorded outputs.
- `POST /api/research-orchestrator/stages/{stage_type}/execute`
  - Executes a specific research stage (`backtest_simulation`, `econometric_validation`, `derivatives_pricing_risk`, etc.).
  - Consolidates allowlisted stage backend mappings directly at the research service.
  - Automatically synchronizes stage execution records into the associated research task run when `run_id` is supplied.

### 2.2 Research Knowledge & Mutation APIs
- `GET /api/research/tickets` & `POST /api/research/tickets`
  - Lists and creates research tickets.
- `GET /api/research/experiments` & `POST /api/research/experiments`
  - Lists and creates research experiments.
- `POST /api/research/experiments/{experiment_id}/actions/{action}`
  - Action mutations (`cancel`, `retry`, `archive`, `invalidate`).
- `GET /api/research/notes` & `POST /api/research/notes`
  - Lists and creates research notes.

When `RESEARCH_DATABASE_URL` (or `RESEARCH_POSTGRES_DSN`) is missing or unconfigured, write routes fail closed with HTTP 503 `service_unavailable`, ensuring zero silent mock writes.

## 3. Failure Handling & Backend Constraints

1. **No Silent Fallback to Stubs**:
   - When a caller requests a real backend (e.g. `PANTHEON_VECTORBT_BACKEND=real`) and the required dependency or provider is unavailable, the service fails closed with HTTP 400/503.
   - List datasets (such as `bars` or `source_refs`) are strictly validated and never replaced with synthetic prices or synthetic lineage.

2. **QuantLib Analytical Fallback**:
   - For `derivatives_pricing_risk`, pure-Python analytic formulas (Black-Scholes analytic pricing and Greeks) are implemented in `services/research/quantlib/adapter/quantlib_adapter.py` when native C++ QuantLib bindings are not compiled in the runtime environment.
   - Results remain deterministic and verified.

3. **BFF Worker Decoupling**:
   - The Agora interaction worker (`agora/interaction/worker.py`) does not construct a fallback in-process `ResearchDispatcher`.
   - Dispatch operations adopt authoritative `task_id` and `run_id` from the research service.
   - Restarting the BFF does not restart completed stages or erase research execution state.

## 4. Operational Diagnostics & Health Verification

- **Service Health Probe**:
  ```bash
  curl -fsS http://127.0.0.1:8000/health
  ```
- **Focused Test Verification**:
  ```bash
  /tmp/clean-bff-venv/bin/python -m pytest -q \
    services/research/tests/test_research_orchestrator_http_service.py \
    services/research/tests/test_research_single_owner_routes.py \
    services/control-plane/bff/tests/test_agora_chain_001_natural_owner_and_receipt.py \
    services/control-plane/bff/tests/test_agora_research_candidate_governed.py
  ```
