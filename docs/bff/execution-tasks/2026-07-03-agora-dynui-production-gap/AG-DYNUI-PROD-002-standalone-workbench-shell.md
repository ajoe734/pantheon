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

## Review (Claude2, reviewer)

PR #170 was closed unmerged (missing required commit trailers) and replaced
by [#171](https://github.com/ajoe734/execute-plans/pull/171)
(`task/AG-DYNUI-PROD-002-agora-standalone-shell-compliant`, commit
`67c0b0480d0999a2b8318c3d9ad44366f5b2f768`), which carries the identical
source diff plus the required trailers — see the separate
`task/AG-DYNUI-PROD-002-docs-pr171` PR for the detailed #170->#171 evidence
correction in this doc. PR #171 is the PR to merge for this task; its
`integration-gate` check is green.

Reviewed PR #171 (commit `67c0b0480d0999a2b8318c3d9ad44366f5b2f768`) against
this task's scope and acceptance criteria. Read the full diff (`App.tsx`,
`routes/agora.tsx` + new `agora.test.tsx`, `agora/TradingDeskLayout.tsx` +
test, `platform/PlatformShell.tsx`, `platform/hooks.ts`) and independently
re-ran the owner's validation from a fresh clone of the PR branch (not just
trusted the PR description):

- `npx vitest run` (full suite) — 118 files / 1102 tests pass.
- `npx tsc --noEmit -p .` — no type errors.
- `npm run build` — production build succeeds (only the pre-existing >500kB
  chunk-size warning).
- `npx eslint` on the touched files above — clean, exit 0.

Findings:

- The route restructuring in `App.tsx` correctly moves the whole `/agora`
  subtree to be a sibling of `/management` (previously both were children of
  the same `<Route element={<PlatformShellRoute />}>`), so Agora no longer
  renders Management's `TopBar`/`NotificationCenter`/`JobProgressDrawer`/
  `HandoffDrawer`/`BulkResultDrawer`/`RollbackSagaDrawer`. Confirmed by
  `agora.test.tsx`'s `getAllByRole("banner")).toHaveLength(1)` and
  `queryByLabelText(/notification/i)).toBeNull()` assertions, which pass.
- Verified the `useParams()` call in the ancestor `AgoraLayoutRoute` (rendered
  at the parent `/agora` layout route, not the `/agora/strategy-workshop/:workshopId`
  leaf) really does receive `workshopId`. This looked suspicious at first —
  React Router v6's `_renderMatches` gives each nesting level its own
  truncated `matches` slice — but `matchRouteBranch` in
  `@remix-run/router/dist/router.js` pushes every match in a branch with a
  reference to the *same* mutable `matchedParams` object, so by the time
  render happens every match in the branch (including the ancestor's) shares
  the fully-merged param set. Confirmed empirically too:
  `npx vitest run src/routes/agora.test.tsx` including
  `"propagates the :workshopId route param from the leaf route into the
  servant drawer context"` passes. Not a bug.
- `useLiveSseConnection()` extraction is a clean shared-lifecycle refactor:
  `PlatformShell` and `AgoraLayoutRoute` are mutually exclusive route
  branches (disjoint top-level paths), so there is no double-connect/leak
  risk from both mounting the hook.
- `ServantDrawer`'s workshop-context loading/error/loaded states and the
  mobile full-width-overlay / horizontally-scrolling tab bar are real,
  BFF-backed behavior (`getWorkshop()`), not fabricated content — matches the
  "no static placeholder" requirement.
- Grepped the rest of the app for stale assumptions about Agora being nested
  under `PlatformShell`: `TopBar`'s Agora nav item (`navigate("/agora")`) now
  correctly leaves the Management shell entirely rather than staying inside
  it, which is the intended fix, not a regression.

Acceptance-criteria gap (approving with a flagged closeout condition, not
blocking): the packet-level `ai-status.json` acceptance list for this task
still includes "desktop and mobile screenshots show corrected shell" and
"Close only after ... hosted proof." This task's hosted screenshot evidence
is explicitly deferred to `AG-DYNUI-PROD-006` (wave 3, "Hosted E2E and
publish gate for production-level closeout" per this packet's own
`INDEX.md` wave table), which is a reasonable, disclosed split rather than a
silent skip — but the owner must not run `ai-status.sh done` on this task
until either (a) `AG-DYNUI-PROD-006` has produced and linked hosted
desktop+mobile `/agora/trading-room` screenshots that this task's closeout
note can cite, or (b) an equivalent hosted/local-dev-server screenshot is
captured directly for this task. Do not close `AG-DYNUI-PROD-002` to `done`
on source-only evidence.

Approving to `review_approved`.
