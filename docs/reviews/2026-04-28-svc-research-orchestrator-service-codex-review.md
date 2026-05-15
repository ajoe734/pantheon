# SVC-RESEARCH-ORCHESTRATOR-SERVICE Review

Reviewer: Codex
Date: 2026-04-28
Disposition: approved

## Scope Reviewed

- `services/research/main.py`
- `services/research/store.py`
- `services/research/Dockerfile`
- `services/research/tests/test_research_orchestrator_http_service.py`
- `services/research/tests/test_research_orchestrator_compose_activation.py`
- `docker-compose.yml`

## Findings

No blocking findings.

The service exposes health, capability, task, run, status, artifact handoff,
and proposal handoff APIs. Stub dispatch is bounded by
`RESEARCH_ORCHESTRATOR_MAX_ACTIVE_RUNS`, production adapter requests are
rejected in the configured single-VM service boundary, and artifact projections
preserve registry lifecycle separation with `artifact_state=draft` and
`deployment_stage=none`. Compose wires a dedicated service, durable volume,
port/env contract, healthcheck, and smoke-stack URL/dependency without enabling
Qlib/TRL/RL production execution.

## Verification

- `pytest services/research/tests/test_research_orchestrator_http_service.py services/research/tests/test_research_orchestrator_compose_activation.py`
  - Result: `3 passed`
- `docker compose config --quiet`
  - Result: passed

## Residual Notes

The wider worktree contains unrelated in-progress service and orchestrator
changes. This review only covers the task-scoped files listed above.
