# PPLG-002 - Idempotent Create-To-Paper Persona Launch Workflow

Priority: P0

Area: Backend orchestration, governance writes, runtime startup

Depends on: `PPLG-001`

## Goal

Implement the user-facing create workflow that creates a persona and completes
paper capital binding, paper deployment plan, paper approval, RuntimeBinding,
and paper runtime startup.

## Required Work

- Implement `POST /bff/management/personas/paper-launch`.
- Require `Idempotency-Key`.
- Preserve step-level atomic records and audit events.
- Return launch step status, failed step, retryability, trace ID, and linked IDs.
- Implement `GET /bff/management/personas/{id}/readiness`.
- Implement `POST /bff/management/personas/{id}/setup/retry`.
- Treat incomplete setup as `setup_failed` or `repair_required`, not normal draft.

## Acceptance Criteria

- Same idempotency key and same payload replays safely.
- Same idempotency key and different payload returns conflict.
- Happy path reaches paper runtime in dev/test fixtures.
- Failure at each step records failed step and supports retry where safe.
- No canary/live capital pool is bound during create.
- Tests prove the workflow emits all linked IDs and audit metadata.

## Artifacts

- `services/control-plane/bff/main.py` or route module
- `services/control-plane/bff/read_store.py`
- `services/control-plane/governance/*`
- `services/control-plane/bff/tests/*paper_launch*`
- `services/control-plane/bff/BFF_API_CONTRACT.md`
