# SVC-TRAINING-SESSION-SERVICE Review

Reviewer: Codex2
Date: 2026-04-28
Disposition: approved

## Scope Reviewed

- `services/training-session/`
- `services/control-plane/bff/read_store.py` trainer service-backed paths
- `services/control-plane/bff/test_training_session_service_client.py`
- `docker-compose.yml` training-session service wiring

## Findings

No blocking findings.

The service exposes health, session lifecycle, append-only teaching event log, trainer controls, preview, replay read, and replay decision APIs. Replay decisions reject stale expected candidate snapshots and append commit/discard events to the JSONL event log. BFF trainer mutations use `PANTHEON_TRAINING_SESSION_API_URL` / `PANTHEON_TRAINING_SESSION_URL` when configured and return unavailable instead of silently masking a down explicit service. Compose wires `training-session-svc`, durable storage, healthcheck, BFF env, BFF dependency, smoke env, and smoke dependency.

## Verification

- `pytest services/training-session/tests/test_http_service.py services/training-session/tests/test_compose_activation.py services/control-plane/bff/test_training_session_service_client.py -q`
  - Result: `6 passed`
- `docker compose config --quiet`
  - Result: passed

## Residual Notes

The wider worktree contains unrelated in-progress service and orchestrator changes. This review only covers the task-scoped files listed above.
