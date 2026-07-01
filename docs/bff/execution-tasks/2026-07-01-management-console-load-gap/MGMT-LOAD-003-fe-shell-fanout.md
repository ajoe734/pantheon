# MGMT-LOAD-003 - Frontend Shell Fanout Reduction

Owner: Claude
Reviewer: Codex
Parent: `MGMT-GAP-010`
Depends on: `MGMT-LOAD-001`, `MGMT-LOAD-002`

## Problem

`TopBar` fetches full list payloads to render small counts, while
`JobProgressDrawer` fetches jobs again during mount. These shell reads compete
with the page's primary Evidence request and make a two-row page feel slow.

## Scope

- Make `TopBar` consume `/bff/management/shell-summary` for badge counts and
  session/transport truth.
- If shell summary is unavailable, defer full list reads until after route
  primary content has rendered and show honest stale/degraded count states.
- Share or lazily hydrate jobs state so first route load does not issue duplicate
  `/bff/jobs` requests.
- Keep notification-center and heavyweight drawer list hydration behind drawer
  open state or a post-primary-content idle callback.
- Keep accessibility and visible shell status intact while counts are loading,
  stale, or degraded.

## Acceptance

- `/management/evidence` starts no more than two non-primary BFF requests before
  first row or empty state is visible.
- No duplicate `/bff/jobs` request occurs before first row or empty state.
- Tests cover shell summary success, degraded summary, unavailable summary
  fallback, and lazy drawer hydration.
- Hosted probe evidence comes from `MGMT-LOAD-001` or its successor gate.
