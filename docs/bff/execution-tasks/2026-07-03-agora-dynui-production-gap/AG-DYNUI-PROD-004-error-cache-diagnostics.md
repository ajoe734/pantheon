# AG-DYNUI-PROD-004 - Error Diagnostics And Stale Bundle Recovery

Owner: Codex2
Reviewer: Claude
Depends on: none

## Problem

The root Trading Room load failure currently collapses to
`Failed to load Trading Room`. The user cannot see whether the failure is auth,
BFF, stale bundle, schema drift, or network. The cache-header repair is merged,
but the UI still lacks production diagnostics and recovery.

## Scope

- Preserve BFF error status/code/request id/correlation id in the page state.
- Add retry and safe reload behavior for stale deployment/bundle suspicion.
- Add tests for 401/403/404/409/412/500/network failure paths.
- Keep secrets out of the UI and logs.
- Extend probes so hosted checks fail when the page only shows the generic
  failure string.

## Acceptance

- The user-facing error state has actionable diagnostics and retry behavior.
- Browser probes capture BFF statuses, console errors, deployment id, and cache
  headers.
- CI or smoke tests fail on a generic-only `Failed to load Trading Room` state.
- The merged cache-header policy from PR #2845 remains verified.
