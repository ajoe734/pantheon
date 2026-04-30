# SVC-OPENCLAW-HONEST-STACK-SEMANTICS Review

Task: `SVC-OPENCLAW-HONEST-STACK-SEMANTICS`
Owner: Codex2
Reviewer: Codex
Decision: Approved
Reviewed at: 2026-04-30T14:45:00Z

## Scope Check

Approved. The patch makes the honest-stack OpenClaw semantics explicit instead of keeping the stale `facade_only` expectation. The adapter now reports `upstream_client_ready` only when the upstream capability call succeeds, and `upstream_client_degraded` when upstream is absent or degraded. The fail-closed broker, paper, live, and capital-binding gates remain deferred by default.

The session-create degraded path remains safe: it may return the legacy non-retryable `CAPABILITY_DENIED` deferral or the typed retryable `UPSTREAM_UNAVAILABLE` upstream envelope, but it does not activate execution.

## Verification

- `python3 -m pytest services/openclaw-gateway-adapter/test_main.py scripts/test_smoke_openclaw_activation_ready_e2e.py -q` passed with `40` tests.
- `python3 scripts/smoke_openclaw_activation_ready_e2e.py` passed with `13/13` rows.
- `python3 -m py_compile scripts/smoke_honest_stack.py services/openclaw-gateway-adapter/main.py` passed.
- `docker compose config --quiet` passed.

## Notes

The owner log reports that an isolated full compose smoke reached and passed the OpenClaw section, then failed later at BFF `/api/v1/consult/requests` with strict-auth JWT `401`. That is outside this OpenClaw semantics task and does not block approval.
