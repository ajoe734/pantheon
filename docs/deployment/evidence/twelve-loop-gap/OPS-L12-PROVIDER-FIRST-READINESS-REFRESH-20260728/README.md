# OPS-L12 Provider-First Readiness Refresh Evidence

Observation cut: `2026-07-28T19:30:00Z`

This evidence manifest captures refreshed provider-first readiness probes and real fleet dispatch proof without configuration edits.

## Architecture Distinction

- **Supervisor Antigravity CLI Provider** (`antigravity` / `antigravity1-1`): Delivery mode `antigravity` via `.orchestrator/bin/agy`. Active auto worker provider in live supervisor config.
- **OpenClaw Assistant Provider** (`openclaw`): Delivery mode `openclaw_adapter`. Upstream assistant gateway for Management AI/OpenClaw dev bridge integration.

## Direct Forced Probes (Observation Cut `2026-07-28T19:30:00Z`)

- **Antigravity CLI**: `ready=true`, status=`ready`, method=`agy_prompt_oauth` (`OAuth token valid`, primary model `gemini-3.6-flash-low`, checked_at=`2026-07-28T19:29:39Z`).
- **Claude Family Per-Slot Breakdown**:
  - `claude`: `ready=false`, status=`auth_not_ready`, method=`claude_auth_status_refresh` (`Claude CLI authentication is missing or OAuth refresh failed.`, checked_at=`2026-07-28T19:29:28Z`). Fail-closed.
  - `claude2`: `ready=true`, status=`ready`, method=`claude_auth_status_refresh` (`OAuth credentials verified`, checked_at=`2026-07-28T19:29:29Z`). Dispatchable.
  - `claude1-1`: `ready=false`, status=`auth_not_ready`, method=`claude_auth_status_refresh` (`Claude CLI authentication is missing or OAuth refresh failed.`, checked_at=`2026-07-28T19:29:30Z`). Fail-closed.
  - `claude1-2`: `ready=false`, status=`auth_not_ready`, method=`claude_auth_status_refresh` (`Claude CLI authentication is missing or OAuth refresh failed.`, checked_at=`2026-07-28T19:29:31Z`). Fail-closed.
  - `claude1-3`: `ready=false`, status=`auth_not_ready`, method=`claude_auth_status_refresh` (`Claude CLI authentication is missing or OAuth refresh failed.`, checked_at=`2026-07-28T19:29:32Z`). Fail-closed.
  - `claude1-4`: `ready=false`, status=`auth_not_ready`, method=`claude_auth_status_refresh` (`Claude CLI authentication is missing or OAuth refresh failed.`, checked_at=`2026-07-28T19:29:34Z`). Fail-closed.

## Real Fleet Dispatch Raw References

- **Antigravity Worker Run**: `antigravity1-1-20260728T185208Z-a6cb5d2a`
  - Event ID: `evt-20260728T185119Z-ff2129b2`
  - Provider: `antigravity1-1`
  - Started At: `2026-07-28T18:52:08Z`
  - Completed At: `2026-07-28T18:54:45Z` (`Worker exited successfully during supervisor boot reconciliation`)
  - Log: `/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/logs/20260728T185208347695Z-antigravity1-1-antigravity1_1-0bd76c.log`
- **Antigravity Worker Run (19:07Z)**: `antigravity1-1-20260728T190729Z-8aeb78de`
  - Event ID: `evt-20260728T190707Z-b52139da`
  - Started At: `2026-07-28T19:07:29Z`
  - Completed At: `2026-07-28T19:09:57Z` (`Exit code 0: completed successfully`)
  - Log: `/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/logs/20260728T190729417957Z-antigravity1-1-antigravity1_1-840628.log`
- **Today Real Claude2 Supervisor Worker Runs (2026-07-28)**:
  - Run 1: `claude2-20260728T193745Z-8bf75509` (Exit code 143: supervisor reconciliation/termination truth)
    - Log: `/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/logs/20260728T193745782933Z-claude2-claude2-21d117.log`
  - Run 2: `claude2-20260728T195332Z-9d18c7ed` (Exit code 143: supervisor reconciliation/termination truth)
    - Log: `/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/logs/20260728T195332131589Z-claude2-claude2-8ca33a.log`

## Fallback Truth

- **Claude Family Partial Readiness**: Primary `claude` slot and `claude1-1`..`claude1-4` subslots are `auth_not_ready` (fail-closed). However, the `claude2` slot is verified `ready=true` with active supervisor dispatch history today (runs `claude2-20260728T193745Z-8bf75509` and `claude2-20260728T195332Z-9d18c7ed`). The Claude family as a whole has partial readiness, not total fail-closed.
- **Antigravity Success**: Antigravity provider passed forced probe (`ready`) via `agy_prompt_oauth`, successfully executing real supervisor worker runs.

## Verification

- `PANTHEON_PY="$(python3 scripts/dev/provision_python_distribution.py --print-python)" && "$PANTHEON_PY" -m pytest services/openclaw-gateway-adapter/tests/`: 117 passed.
- `bash scripts/openclaw-smoke-test.sh`: 6 passed.

