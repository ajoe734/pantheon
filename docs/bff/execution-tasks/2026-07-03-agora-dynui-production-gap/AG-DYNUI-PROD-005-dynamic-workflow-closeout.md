# AG-DYNUI-PROD-005 - Dynamic Workflow Closeout

Owner: Claude
Reviewer: Codex2
Depends on: `AG-DYNUI-PROD-002`, `AG-DYNUI-PROD-003`, `AG-DYNUI-PROD-004`

## Problem

Workspace proposal preview, grid editor, and widget revision components exist,
but production readiness requires proving that the whole workflow is wired
through strict BFF contracts in the hosted app.

## Scope

- Verify and repair the full flow: proposal generation, proposal acceptance,
  workspace load, layout patch, widget revision proposal, apply, keep-copy,
  version history, and rollback.
- Ensure every mutation uses idempotency, optimistic concurrency, and
  Agora/user scope isolation.
- Confirm WidgetSpec/ChartSpec rendering only uses the allowlisted registry.
- Remove or block any fallback that simulates success without BFF persistence.

## Acceptance

- All V11 dynamic workflow operations work through BFF in strict live mode.
- Tests cover success, conflict, permission, stale etag, and unsupported widget
  paths.
- No arbitrary React/JavaScript/HTML is generated or injected.
- Closeout records backend and execute-plans PRs, merge SHAs, and hosted proof.
