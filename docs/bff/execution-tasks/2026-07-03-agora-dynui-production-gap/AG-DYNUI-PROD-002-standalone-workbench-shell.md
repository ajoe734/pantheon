# AG-DYNUI-PROD-002 - Agora Standalone Workbench Shell

Owner: Claude
Reviewer: Codex
Depends on: `AG-DYNUI-PROD-001`

## Problem

The hosted Agora route is currently mounted inside the global PlatformShell and
then inside a three-tab TradingDeskLayout. This makes Agora look and behave like
an embedded management tab, not the design-pack workbench.

## Scope

- In execute-plans, make `/agora/*` render through an intentional Agora
  workbench shell, or document and implement an approved shell exception.
- Preserve auth, live status, notifications, and BFF connectivity without
  leaking Management IA into the Agora canvas.
- Replace placeholder servant drawer content with a real contextual shell state
  or a blocker if the data contract is missing.
- Keep routes deep-linkable and mobile-safe.

## Acceptance

- `/agora/trading-room` no longer accidentally inherits unrelated management
  chrome, or an explicit approved exception is documented and visible in tests.
- Agora navigation matches the design-pack workbench IA rather than only the
  old three-tab skeleton.
- Shell tests cover routing, top chrome, drawer/bottom surface behavior, and
  responsive layout.
- Hosted screenshot evidence shows the corrected shell.

## Implementation Notes (2026-07-04)

### Critical source-location correction

The pantheon-vendored `execute-plans/` mirror in this repo (edited by
`AG-FE-DYNUI-001..005` historically and, more recently, `AG-DYNUI-PROD-004`)
is **not** the canonical frontend source. It is a `.gitignore`d "sibling
repository" checkout (`.gitignore:48-50`,
`.orchestrator/multi_repo_registry.py`) that only carries whichever files a
prior task force-added into pantheon history; it has drifted into an
entirely different, simpler architecture (multi-entry Vite apps, no router,
no `App.tsx`, no `PlatformShell`) than the real, hosted app.

Per `docs/frontend/execute-plans-dev-hosting.md` and confirmed independently
by cloning `origin/dev` fresh (`git clone --branch dev
https://github.com/ajoe734/execute-plans.git`), the real
`ajoe734/execute-plans` repo at `origin/dev` (`702b236a`, matching the
`/deployment.json` commit the sidecar packet observed on the hosted dev FE)
has `src/App.tsx`, `src/platform/PlatformShell.tsx`, and
`src/routes/agora.tsx` exactly as this task's `artifacts` list names them —
this confirms the task brief was written against the real repo, and the
correct place to make this shell-architecture change is a clean task
worktree of `ajoe734/execute-plans`, not the pantheon-vendored mirror.
Do not repeat the `AG-FE-DYNUI-*`/`AG-DYNUI-PROD-004` mistake of editing the
vendored copy for new Agora frontend work; any prior task that only touched
`pantheon/execute-plans/*` never reached the real hosted app and should be
re-verified against `ajoe734/execute-plans` before being trusted as shipped.

### What changed

Implemented in `ajoe734/execute-plans` PR
[#170](https://github.com/ajoe734/execute-plans/pull/170)
(`task/AG-DYNUI-PROD-002-agora-standalone-shell`, base `dev`):

- `src/App.tsx`: moved the `/agora` route tree out of
  `<Route element={<PlatformShellRoute />}>` so it is a sibling of
  `/management`, while remaining inside `AuthProvider` / `TooltipProvider` /
  `ErrorBoundary` (all wrap `<Routes>` above both route trees already).
- `src/routes/agora.tsx`: `AgoraLayoutRoute` now renders `LiveStatusBanner` +
  `TradingDeskLayout` directly (no Management `TopBar`,
  `NotificationCenter`, `JobProgressDrawer`, `HandoffDrawer`,
  `BulkResultDrawer`, or `RollbackSagaDrawer`), and reads `:workshopId` via
  `useParams()` to pass into `TradingDeskLayout` — previously a dead prop,
  since the route wrapper never read it.
- `src/platform/hooks.ts` / `src/platform/PlatformShell.tsx`: extracted
  `useLiveSseConnection()` so both the Management and Agora shells share one
  connect/disconnect lifecycle against the same `liveStatus`/SSE substrate,
  preserving live-status visibility for Agora exactly as `LiveStatusBanner`'s
  own header comment declares it should cover ("Management Console, Agora,
  and v5 page headers").
- `src/agora/TradingDeskLayout.tsx`: `ServantDrawer` now loads real workshop
  context (`getWorkshop()`: subject title, status, message count) instead of
  a static "workshop context loads here" placeholder, with a loading state
  and a degraded `role="alert"` state when the fetch fails — no fabricated
  content. Servant drawer becomes a fixed full-width overlay below a 768px
  viewport instead of a fixed 340px column, and the tab bar scrolls
  horizontally instead of clipping, for mobile-safe layout.
- Added `src/routes/agora.test.tsx` (route composition: standalone shell, no
  Management chrome, live-status preserved, `:workshopId` propagation) and
  extended `src/agora/TradingDeskLayout.test.tsx` (viewport/responsive drawer,
  workshop-context loading/loaded/error states).

### Verification

- `npm test` — 118 files / 1102 tests pass (full suite, not just touched
  files).
- `npm run build` — production build succeeds.
- `npx tsc --noEmit` — no type errors.
- `npx eslint` on touched files — clean.
- PR #170 CI (`Pantheon FE-BFF Integration Gate`, `pull_request` trigger)
  was still running at hand-off time; hosted browser/mobile screenshot
  evidence for the deployed dev FE is explicitly deferred to
  `AG-DYNUI-PROD-006` (hosted E2E/publish gate), consistent with this
  fleet's wave routing — this task closes the shell-architecture gap in
  source, not the hosted-deploy proof.

### Residual risks

- `AG-DYNUI-PROD-004`'s reviewer-approved changes only exist in the
  pantheon-vendored mirror; they have not reached `ajoe734/execute-plans`
  and therefore are not live on the hosted dev FE. This is a pre-existing gap
  from before this task and is out of this task's scope to fix, but is
  flagged here so `AG-DYNUI-PROD-006` (hosted E2E/publish gate) does not
  assume `AG-DYNUI-PROD-004`'s diagnostics work is already deployed.
- PR #170 CI depends on live BFF secrets
  (`PANTHEON_BFF_OIDC_CLIENT_ID`/`SECRET`) for its authenticated-smoke and
  SSE-soak steps; if those are unavailable to this run, a human/CI owner
  should confirm the failure is infra-related, not a regression from this
  change, before merging.
