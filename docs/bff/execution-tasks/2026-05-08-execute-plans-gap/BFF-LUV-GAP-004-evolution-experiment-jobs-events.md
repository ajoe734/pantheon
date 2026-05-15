# BFF-LUV-GAP-004 - Evolution, Experiments, Jobs, And Events BFF Compatibility

Priority: P1

Area: Long-running work, evolution, experiment, and event read surfaces

## Goal

Expose the execution-plan surfaces for evolution programs, experiments, jobs, and event feeds.

## Missing Routes

Evolution:

- `GET /bff/evolution-programs`
- `POST /bff/evolution-programs`
- `GET /bff/evolution-programs/{programId}`
- `PATCH /bff/evolution-programs/{programId}`
- `GET /bff/evolution-programs/{programId}/runs`
- `GET /bff/evolution-programs/{programId}/candidates`
- `POST /bff/evolution-programs/{programId}/actions/{actionId}`

Experiments:

- `GET /bff/experiments`
- `POST /bff/experiments`
- `GET /bff/experiments/{experimentId}`
- `POST /bff/experiments/{experimentId}/actions/{actionId}`
- `GET /bff/experiments/{experimentId}/logs`
- `GET /bff/experiments/{experimentId}/metrics`
- `GET /bff/experiments/{experimentId}/artifacts`

Jobs and events:

- `GET /bff/jobs`
- `GET /bff/jobs/{jobId}`
- `GET /bff/jobs/{jobId}/logs`
- `POST /bff/jobs/{jobId}/actions/{actionId}`
- `GET /bff/events`
- `GET /bff/events/stream`

## Implementation Notes

- Reuse existing job/runtime/evolution read-store projections where possible.
- `GET /bff/events/stream` can delegate to the SSE compatibility implementation in `BFF-LUV-GAP-010`.
- Long-running actions must return job or command envelopes, not ad-hoc strings.

## Acceptance Criteria

- All listed routes are non-404.
- Jobs expose stable status, progress, logs, and action results.
- Experiments and evolution runs expose artifact links using existing artifact/linkage semantics.
- Tests include unavailable-backend degradation behavior.

## Implementation Status

Status: implemented; pending reviewer approval.

Delivered in `services/control-plane/bff/main.py`:

- Added execute-plans compatibility handlers for `/bff/evolution-programs`, `/bff/experiments`, `/bff/jobs`, and `/bff/events`.
- Evolution program and experiment create/update routes keep BFF-local overlays for round-trip compatibility when no durable writer backend is available.
- Action routes use final command envelopes through `CommandType.EVOLUTION_PROGRAM_ACTION`, `CommandType.EXPERIMENT_ACTION`, and `CommandType.JOB_ACTION`.
- Job detail/log routes expose stable `status`, `progress`, and `logs` fields.
- Experiment logs, metrics, and artifacts expose existing linkage fields without frontend-side inference.
- `/bff/events` reads the governance audit event surface and returns an empty degraded payload when that backend is unavailable.
- `/bff/events/stream` remains delegated to the GAP-010 SSE compatibility substrate.
- GAP-004 route registration now prunes earlier shadow placeholder handlers so the task-scoped compatibility handlers are the actual FastAPI routes.

Verification:

- `pytest services/control-plane/bff/test_bff_evolution_experiment_jobs_events_contract.py -q`
- `pytest services/control-plane/bff/test_execute_plans_contract_registry.py services/control-plane/bff/test_pkt005_sse_substrate_contract.py -q`
- `pytest services/control-plane/bff/test_bff_governance_runtime_risk_audit_contract.py -q`
- `python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/test_bff_evolution_experiment_jobs_events_contract.py`
