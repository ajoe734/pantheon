# AG-DYNUI-FULL-005 Live Dynamic Workflow

Owner: Codex
Reviewer: Codex2
Status: todo.

## Scope

Make the full dynamic Agora workflow work for a real browser-scoped workshop:

1. Create or restore a workshop through live BFF.
2. Produce cards and readiness through live BFF.
3. Advance readiness to `trading_room` through public API/UI behavior.
4. Materialize a real `strategyId` and `strategyVersion`.
5. Load Trading Room with the explicit strategy route.
6. Generate a workspace proposal.
7. Accept the proposal and receive a workspace ETag.
8. Patch grid layout with `If-Match` and `Idempotency-Key`.
9. Propose and apply a widget revision.
10. List version history.
11. Roll back and show the new version.

## Required Evidence

- Live request transcript with correlation/request ids.
- No `page.route()` or mocked BFF responses on the production gate path.
- Tenant/user scope proof: browser user owns the workshop, readiness, strategy,
  workspace, versions, and rollback result.
- WidgetSpec allowlist and optimistic concurrency remain enabled.
- Local BFF tests and any required execute-plans tests pass.
- PRs, merge SHAs, deploy SHAs, and post-deploy curls are recorded.

## Current Blockers

- Browser-created workshop `ce63ec2a-c5f1-4e41-8219-e410d22037c7` has live
  cards/readiness but is blocked from `trading_room` by missing completeness
  and Strategy Registry references.
- Browser-scoped Trading Room aggregate still returns `strategies: []`.
- `AG-DYNUI-FULL-004` PR #185 must land before the hosted UI can navigate with
  the final strategy route context.
