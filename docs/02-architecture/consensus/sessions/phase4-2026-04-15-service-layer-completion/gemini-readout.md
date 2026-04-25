# Gemini Readout — Phase 4: Service Layer Completion

> **Role**: Stress-test runtime, replay, and tooling feasibility.
> **Date**: 2026-04-15
> **Status**: Submitted

## 1. Executive Summary (Stress-Test Perspective)

The transition from "contract/model complete" to "deployable service complete" is the critical path for Phase 4. Current repo state relies heavily on local JSON snapshots and hardcoded fallbacks (e.g., `services/control-plane/bff/read_store.py`). To achieve a verifiable Single-VM deployment and support the "Golden Replay" scenarios, the service layer must move from domain objects to first-class HTTP services with stable port/env/volume contracts.

## 2. Cited Observations & Risks

### 2.1 Runtime Feasibility (Single-VM Resource Constraints)
- **Observation**: The target environment is a single VM (8 vCPUs, 16-32GB RAM) for testing (`Pantheon_單VM測試版_雙VM正式版_部署補充說明.md`).
- **Observation**: Multiple heavy research/learning workers (Qlib, DSPy, imitation, FinRL, RLlib) are part of the stack (`RESEARCH_BACKEND_MATURITY_MATRIX.md`).
- **Risk**: Concurrent execution of heavy ML workers alongside the core control plane and LEAN execution engine may lead to resource contention or OOM during replay cycles.
- **Recommendation**: Implement explicit Docker resource limits (`mem_limit`, `cpus`) in `docker-compose.test.yml`, specifically for research workers.

### 2.2 Replay & Lineage Feasibility (GAP-05 Acceptance)
- **Observation**: GAP-05 (Golden Replay) requires a deterministic re-execution producing "durable storage verification" in Postgres/Redis (`GOLDEN_REPLAY_SCENARIO_AND_RUNBOOK.md §1`).
- **Observation**: Lineage tracing from `RawDataset` to `RuntimeBinding` is a mandatory sign-off criterion (`GOLDEN_REPLAY_SCENARIO_AND_RUNBOOK.md §6`).
- **Risk**: Currently, `telemetry-ingest` and `lineage-read` lack Dockerfiles and service wrappers (`phase2-phase6-gap-inventory.md §5`). Without these as stable services, the lineage trace cannot be verified via the BFF/API path.
- **Recommendation**: Prioritize the "serviceization" of the Telemetry and Lineage planes to unblock Replay verification.

### 2.3 Tooling & Port Feasibility (Infrastructure Gaps)
- **Observation**: A known port conflict exists where both Router and BFF bind to `8001` in local runners (`phase2-phase6-gap-inventory.md §5`).
- **Observation**: Router's Dockerfile explicitly exposes `8001` (`services/control-plane/router/Dockerfile`).
- **Observation**: BFF (`services/control-plane/bff`) and several core services currently lack Dockerfiles (`phase2-phase6-gap-inventory.md §5`).
- **Recommendation**: Establish a canonical port map for the single-VM stack to prevent collisions. Suggested:
    - `8000`: Web Channel
    - `8001`: Router
    - `8002`: Persona
    - `8003`: BFF (Operator API)
    - `8004`: Runtime Manager / Control Plane Internal
    - `8005`: Governance / Registry Core
    - `8006`: Telemetry Ingest
    - `8007`: Lineage Read

## 3. Tooling & Delivery Order Feasibility

I concur with the `starter-draft.md` priority, specifically the "SVC-BASELINE" lock. However, from a feasibility standpoint, the **BFF Rewiring** (`SVC-SURFACES`) should be interleaved with serviceization to ensure that each new service is immediately verified by the operator dashboard.

### 3.1 OpenClaw Dependency
- **Observation**: OpenClaw is currently in `adapter-started` state (`OSS_INTEGRATION_CHECKLIST.md`).
- **Risk**: Since the Persona runtime model depends on this contract (`OPENCLAW_RUNTIME_CONTRACT.md`), any delay in the OpenClaw adapter will leave a "black hole" in the execution plane during single-VM testing.
- **Recommendation**: Maintain the `mock_ex001` execution feedback as a fallback for Phase 4 to ensure the Control Plane can be stressed even if the OpenClaw/LEAN path is unstable.

## 4. Feasibility Verdict

The proposed plan is **feasible** provided that:
1. Dockerfiles are standardized following the pattern in `services/control-plane/persona/Dockerfile`.
2. Port allocations are strictly governed.
3. Resource limits are applied to non-core ML workers.
