# MGMT-GAP-001 - Management Route And IA Cleanup

Owner: Codex2
Reviewer: Claude
Batch: 1
Fleet lane: Frontend IA and route cleanup
Status: done

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

## Closeout Evidence

Closed by `ajoe734/execute-plans` PR #120:
`https://github.com/ajoe734/execute-plans/pull/120`.

| Item | Evidence |
|---|---|
| Implementation commit | `806f53fe5e9ac6e0e909621ba0c13b775679adc7` |
| FE merge/deploy commit | `6218e67d4119bcfc663681935d2a98e5af73e55a` |
| Dev integration gate | `https://github.com/ajoe734/execute-plans/actions/runs/28452500411` |
| Dev deploy | `https://github.com/ajoe734/execute-plans/actions/runs/28452499928` |
| Hosted deployment | `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json` |
| BFF health | `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/healthz` |
| Archive | `docs/04/pantheon_management_console_gap_2026-06-30/archive/mgmt-gap-001-closeout-2026-06-30.md` |

Hosted browser probe after deploy proved:

- `/management/control-room-legacy -> /management/cockpit`
- `/management/deployment -> /management/deployments`
- `/management/deployment/dep-9?tab=events -> /management/deployments/dep-9?tab=events`
- primary nav excludes Formula Studio, Skill Sandbox, and loop subpages;
  `/management/loops` remains.

## Second-Pass Note

The 2026-07-01 route/control re-audit confirmed the legacy list aliases still
redirect correctly, including `/management/control-room`, `/management/one-ring`,
`/management/overview`, `/management/command-center`, `/management/risk-center`,
`/management/capital-pools`, `/management/ranking-formulas`,
`/management/rebalances`, `/management/research`, and `/management/deployment`.

It also found detail aliases that still direct-render:
`/management/capital-pools/:id`, `/management/ranking-formulas/:id`,
`/management/rebalances/:id`, and `/management/research/:id`. Those are not a
reopen of `MGMT-GAP-001`; they are tracked under `MGMT-GAP-008` because the fix
is detail DTO/canonical mapper honesty.
