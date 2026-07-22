# MGMT-GAP-008 Closeout - Detail DTO And Render Honesty

Date: 2026-07-01

## Status

MGMT-GAP-008 is complete for the detail-page DTO/render-honesty scope.

This closeout records the external `execute-plans` delivery evidence so the
Pantheon task board can unlock the harness and final-closeout gaps that
depend on it.

## Delivery Evidence

- execute-plans PR: https://github.com/ajoe734/execute-plans/pull/133
  ("fix detail DTO/render honesty")
- PR #133 merge commit: `225765a81cbbaa9f958c0d9e97627425f555f5e2`
- Follow-up execute-plans PR: https://github.com/ajoe734/execute-plans/pull/135
  ("fix remaining id/name/kind and capability-loading gaps") — landed after a
  hosted Playwright probe against the PR #133 fix found three remaining live
  bugs: blank experiment h1, `undefined · v1` artifact subtitle, and
  Tool/MCP/Skill detail pages stuck on the loading spinner for missing seed
  ids (unhandled `.catch()` on 404).
- PR #135 merge commit: `47b8f4182c58c072cfa484de81ef21754e9e6415`
- Dev FE deploy run: `28515196491`, conclusion `success`
- Dev FE-BFF integration gate run: `28515196527` (job `84521764161`),
  conclusion `success`
- Hosted `/deployment.json` at `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`
  reports commit `47b8f4182c58c072cfa484de81ef21754e9e6415` with
  `VITE_BFF_MODE=live` and `VITE_BFF_FALLBACK=strict`, confirming the PR #135
  fix is the deployed dev FE build.
- Reviewer (Codex2) recorded local validation (`tsc`, `eslint` on changed
  files, targeted `vitest`, `vite build`, `check-management-naming`) and the
  remote integration-gate rerun as a PR comment on #135, since the same
  GitHub account authored and would have approved the PR.

## Delivered Fixes

- `seed.ts`: non-destructive `normalizeBaseObjectFields` / `normalizeArtifactFields`
  DTO normalizers filling missing `state`/`risk`/`name`/`owner`/`updatedAt`/`id`
  from known live-BFF field aliases, without overwriting real data.
- Honest "Unavailable" badge fallback in `StatusBadge`/`RiskBadge`; safe
  percent/ratio helpers eliminating `NaN%` stat cards.
- Reusable alias-redirect factory (generalized from `DeploymentAliasRedirect`)
  covering `capital-pools/:id`, `ranking-formulas/:id`, `rebalances/:id`,
  `research/:id`.
- Explicit "live registry empty" / `CapabilityDetailEmptyState` for
  Tool/MCP/Skill detail pages, including a `.catch()` on the BFF detail
  fetch so a 404 resolves the loading state instead of spinning forever.

## Follow-On Gates

- `MGMT-GAP-006` (hosted acceptance harness) still waits on `MGMT-GAP-004`,
  `MGMT-GAP-005`, `MGMT-GAP-009`, and `MGMT-GAP-010` in addition to this task.
- `MGMT-GAP-007` (final closeout archive) remains blocked on `MGMT-GAP-006`.

## Residual Risks

- This does not close command/write truth gaps; that remains `MGMT-GAP-004`.
- This does not cover session/RBAC contract consistency; that remains
  `MGMT-GAP-009`.
- This does not replace the final strict-live hosted acceptance harness; that
  remains `MGMT-GAP-006` and `MGMT-GAP-007`.
