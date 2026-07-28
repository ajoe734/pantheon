# OPS-L12 Provider-First Readiness Refresh Evidence

Observation cut: `2026-07-28T19:08:35Z`

This evidence manifest captures refreshed provider-first readiness probes and real fleet dispatch proof without configuration edits.

## Architecture Distinction

- **Supervisor Antigravity CLI Provider** (`antigravity` / `antigravity1-1`): Delivery mode `antigravity` via `.orchestrator/bin/agy`. Active auto worker provider in live supervisor config.
- **OpenClaw Assistant Provider** (`openclaw`): Delivery mode `openclaw_adapter`. Upstream assistant gateway for Management AI/OpenClaw dev bridge integration.

## Direct Forced Probes (Observation Cut `2026-07-28T19:08:35Z`)

- **Antigravity CLI**: `ready=true`, status=`ready`, method=`agy_prompt_oauth` (`OAuth token valid`, primary model `gemini-3.6-flash-low`).
- **Claude CLI**: `ready=false`, status=`auth_not_ready`, method=`claude_auth_status_refresh` (`Claude CLI authentication missing or OAuth refresh failed`).

## Real Fleet Dispatch Raw References

- **Antigravity Worker Run**: `antigravity1-1-20260728T185208Z-a6cb5d2a`
  - Event ID: `evt-20260728T185119Z-ff2129b2`
  - Provider: `antigravity1-1`
  - Started At: `2026-07-28T18:52:08Z`
  - Completed At: `2026-07-28T18:54:45Z` (`Worker exited successfully during supervisor boot reconciliation`)
  - Log: `/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/logs/20260728T185208347695Z-antigravity1-1-antigravity1_1-0bd76c.log`
- **Current Active Antigravity Run**: `antigravity1-1-20260728T190729Z-8aeb78de`
  - Event ID: `evt-20260728T190707Z-b52139da`
  - Started At: `2026-07-28T19:07:29Z`

## Fallback Truth

- **Claude Fail-Closed**: Claude provider failed forced auth probe (`auth_not_ready`), correctly preventing new Claude worker dispatches.
- **Antigravity Success**: Antigravity provider passed forced probe (`ready`) via `agy_prompt_oauth`, successfully executing real supervisor worker runs.

## Verification

- `PANTHEON_PY="$(python3 scripts/dev/provision_python_distribution.py --print-python)" && "$PANTHEON_PY" -m pytest services/openclaw-gateway-adapter/tests/`: 117 passed.
- `bash scripts/openclaw-smoke-test.sh`: 6 passed.
