# MGMT-GAP-001 - Management Route And IA Cleanup

Owner: Codex2
Reviewer: Claude
Batch: 1
Fleet lane: Frontend IA and route cleanup

## Problem

The management console still contains hidden legacy routes and an overloaded
first-level nav. The most important route defect is
`/management/control-room-legacy`, which still renders the old Control Room.
`/management/deployment` and `/management/deployment/:id` also duplicate the
canonical `/management/deployments` family.

## Scope

- Redirect or remove `/management/control-room-legacy`.
- Redirect `/management/deployment` to `/management/deployments`.
- Redirect `/management/deployment/:id` to `/management/deployments/:id`.
- Keep bookmark-preserving redirects that already land on canonical routes.
- Reduce first-level nav exposure for non-production surfaces:
  - studios;
  - empty/degraded registries without real commands;
  - loop subpages that can be reached from the loop overview.
- Add or update route tests covering canonical and legacy routes.

## Non-Scope

- Do not delete operator-distinct pages just because they share entities.
- Do not remove `human-inbox`, `sentinel`, `interventions`, `approvals`, or
  `governance`; consolidate them only when a production IA design is present.

## Acceptance

- Hidden route probe shows `control-room-legacy` no longer renders old
  `ControlRoomPage`.
- Deployment singular routes redirect to canonical plural routes.
- Visible nav count is intentionally reduced or every remaining first-level item
  has a production readiness note.
- Tests cover route redirects and old aliases.
- Hosted FE after merge proves the final paths.
