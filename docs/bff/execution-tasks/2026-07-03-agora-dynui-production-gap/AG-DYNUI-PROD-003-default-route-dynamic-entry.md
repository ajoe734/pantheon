# AG-DYNUI-PROD-003 - Trading Room Default Dynamic Entry

Owner: Claude2
Reviewer: Codex
Depends on: `AG-DYNUI-PROD-001`

## Problem

The default `/agora/trading-room` path can render an empty aggregate view:
`All Strategies`, `No strategies in the Trading Room`, empty queue, and empty
position actions. The dynamic proposal workflow is only reached when a strategy
id and strategy version are present.

## Scope

- Define and implement the default Trading Room entry state from live BFF data.
- If no strategy is ready, route the operator into the Strategy Workshop or a
  design-pack dynamic readiness flow instead of a dead empty shell.
- If a ready strategy exists, enter the workspace proposal preview path without
  requiring manual URL surgery.
- Keep the state honest: no hardcoded fake strategies and no static mock
  dashboard.

## Acceptance

- Hosted `/agora/trading-room` never lands on an inert empty table shell without
  a meaningful dynamic next action.
- Strategy selection, readiness, proposal generation, and back-to-workshop
  behavior are tested.
- Empty, loading, degraded, and no-ready-strategy states are driven by BFF data.
- Live screenshot evidence covers no-strategy and ready-strategy cases.

## Review (Claude2, reviewer)

Reviewed commit `eab6e0cfd` (PR #2860, merged `ec5d902fc` into `dev`) against
this task's scope and acceptance criteria. Read the full diff (`TradingRoomPage.tsx`,
`TradingRoomPage.test.tsx`, `entries/agora-main.tsx`) and independently re-ran
the owner's validation:

- `npm test -- --run src/agora/pages/trading-room/TradingRoomPage.test.tsx` —
  51/51 pass.
- `npm test -- --run src/lib/bff-v1/agora/tradingRoom.test.ts src/agora/pages/strategy-workshop/StrategyWorkshopPage.test.tsx`
  — 42/42 pass (confirms the "Not changing" claim: BFF lib and Strategy
  Workshop internals are untouched and still green).
- `npm run build:agora` — builds cleanly, only the pre-existing >500kB chunk
  warning.

Findings:

- `selectDefaultReadyStrategy` auto-enters the highest-value `ready` strategy
  (dashboard-recipe-first, then pending-event volume, then monitoring
  priority, then title as a stable tiebreaker) via `effectiveStrategyId`, so a
  ready strategy reaches the workspace/proposal view with no manual URL
  surgery, satisfying that acceptance line.
- When no strategy is `ready`, `TradingRoomDefaultEntry` renders either the
  readiness-row grid (`trading-room-readiness-entry`, one card per strategy
  with the actual `readiness_state`/`monitoring_state`/candidate/pending
  counts) or the workshop-empty-entry card when the BFF returns zero
  strategies — both always carry an actionable "Open Strategy Workshop" CTA,
  so the hosted default route can no longer land on the old inert
  `strategy-list-table` shell.
- `readinessReason()` and the `MONITORING_PRIORITY` / readiness sort order
  are exhaustive over the real BFF union types (`readiness_state: "blocked" |
  "conditional" | "ready" | "stale"`, `monitoring_state` 5-way) from
  `tradingRoom.ts` — no silent fallthrough for an unhandled state.
- `onOpenWorkshop` is real routing, not a stub: `agora-main.tsx` wires it to
  `handleTabChange("strategy-workshop")`, which pushes `/agora/strategy-workshop`
  and mounts `StrategyWorkshopPage`. No hardcoded/fake strategy data is
  introduced anywhere in the diff.
- `TradingEventQueue` and `PositionActionQueue` (previously rendered by the
  removed `AggregateView`) are still referenced from `StrategyWorkspaceView`,
  so removing the old aggregate view did not orphan that code.

Approving. Closeout note for the owner: this task's acceptance list still
calls for "Live screenshot evidence covers no-strategy and ready-strategy
cases," which is not yet present on this task doc or the PR. Per the
AG-DYNUI-PROD-004 precedent, hosted proof needs a human-gated dev deploy
dispatch — capture that screenshot evidence (or an explicit local-dev-server
equivalent) before finalizing this task to `done`.

## Owner Closeout (Claude, 2026-07-04)

Re-verified the reviewer's approval is still accurate against the current
worktree, then worked the remaining closeout gap. Three prior sidecar
packets (`AG-DYNUI-PROD-003-SIDECAR-BFF-HANDOFF{,-FOLLOWUP-2,-FOLLOWUP-3}`)
had already found the real blocker behind "hosted proof still owed": the
hosted dev FE deploys from the **standalone** `ajoe734/execute-plans` repo,
not this monorepo's in-tree `execute-plans/` mirror that PR #2860 landed in,
and that standalone repo had never received this feature (independently
diverged — grid editor, widget revision drawer, workspace proposal flow all
postdate the mirror's fork point).

Work done this pass:

1. **Ported the fix to the standalone repo.** Cloned `ajoe734/execute-plans`
   fresh (avoided the shared, already-dirty `/home/lupin/code/execute-plans`
   checkout per the anchor-commit worktree-safety rule), re-implemented
   `selectDefaultReadyStrategy` / `TradingRoomDefaultEntry` directly against
   that repo's current `TradingRoomPage.tsx` (replacing its inert
   `AggregateView`/`StrategyList` default), and wired `onOpenWorkshop` through
   `AgoraTradingRoomRoute` using the existing `onBackToWorkshop`
   `navigate(...)` convention. Opened
   [ajoe734/execute-plans#173](https://github.com/ajoe734/execute-plans/pull/173)
   (`task/AG-DYNUI-PROD-003-default-route-dynamic-entry` → `dev`).
   Validation on that repo: `npx vitest run
   src/agora/pages/trading-room/TradingRoomPage.test.tsx` (56/56),
   `npx vitest run` full suite (117 files / 1093 tests), `npx tsc --noEmit -p .`
   (clean), `npm run build` (passes, pre-existing >500kB chunk warning only).
   `integration-gate` CI check on PR #173: **pass**.
2. **Captured the still-outstanding screenshot evidence** using a
   local-dev-server (the reviewer's explicitly sanctioned fallback when
   hosted deploy isn't current) built from PR #173's branch, proxied through
   Vite's dev-server BFF proxy against the **real live dev BFF** — see
   `docs/deployment/evidence/ag-dynui-prod-003/20260704T032550Z/README.md`
   for full methodology:
   - No-strategy case: genuine live-BFF screenshot (confirmed via direct
     `curl` that the live tenant scope has zero strategies right now — not a
     fixture).
   - Ready-strategy case: the live dev BFF scope has no ready (or any)
     strategy and dev writes are disabled, so no live tenant data exists to
     demonstrate this path without fabrication. Captured against the same
     real build using a Playwright network-level route mock of the BFF
     contract shape (not a product-code fixture) — documented as such,
     not represented as live tenant data.

**Still blocked on human-gated steps** (do not attempt to force these; see
`project_agora_pr_self_merge_governance_block` — self-merge on
`ajoe734/execute-plans` PRs is consistently blocked by the harness's
auto-mode classifier even when CI is green and review is clean):

1. A human/chair needs to merge
   [ajoe734/execute-plans#173](https://github.com/ajoe734/execute-plans/pull/173)
   into that repo's `dev`.
2. Re-run the hosted browser probe against the redeployed host to replace
   the local-dev-server evidence with true hosted screenshots.

This task stays in `review_approved` (not `done`) until those steps land —
closing it now would misrepresent an unmet "hosted proof" acceptance line.

**Re-verification (2026-07-04):** PR #173 unchanged — still
OPEN/MERGEABLE/CLEAN, `integration-gate` SUCCESS at `2026-07-04T03:28:21Z`,
`autoMergeRequest=null` (self-merge still blocked). Corrected a prior
assumption while re-checking the deploy path: this repo's own
`Pantheon Nonprod Deploy` workflow (`.github/workflows/nonprod-deploy.yml`)
is irrelevant here — `services/control-plane/bff/agora/trading_room.py`
was never actually created/touched by this task, so there is no in-tree
BFF component to redeploy. The hosted FE for this task is served from the
**standalone** `ajoe734/execute-plans` repo, whose own
`Pantheon Dev FE Deploy` workflow
(`.github/workflows/pantheon-dev-fe-deploy.yml` in that repo) has
auto-deployed on every push to its `dev` branch since commit `37332ee92`
(2026-06-19, "auto-deploy on merge to dev (decouple from integration
gate)") — no separate manual `workflow_dispatch` is required. So the
remaining human-gated surface is a single step (merge PR #173); the
redeploy and hosted re-probe follow without an extra dispatch ask. Not
re-notifying the human again beyond this doc correction — the actual
blocking action (merge PR #173) is unchanged and already surfaced
repeatedly.

**Re-verification (2026-07-04, subsequent pass):** confirmed no state change
since the prior pass. `gh pr view 173 --repo ajoe734/execute-plans` shows the
exact same `headRefOid` (`2b054ab9f`), `OPEN`/`MERGEABLE`/`CLEAN`, zero
reviews, `autoMergeRequest=null`, and `integration-gate` `SUCCESS` at the same
timestamp (`2026-07-04T03:28:21Z`). Pantheon-side commit `7b360cb60` is still
an ancestor of `origin/dev`. `orchestrator_approval_broker` MCP is still not
resolvable via `ToolSearch`. No pantheon-side action is available this pass;
the sole remaining blocker is unchanged — a human/chair merging
`ajoe734/execute-plans#173`. Zero commits this pass on the parent lane.

A parallel sidecar (`AG-DYNUI-PROD-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-4`,
reviewed by this owner and merged `done`) added one new fact worth recording
here: `gh api repos/ajoe734/execute-plans/branches/dev/protection` returns
`404 Branch not protected`, and all three merge methods are enabled on that
repo. This confirms the remaining block on PR #173 is a **self-imposed AI
governance policy** (no self-merge of a PR this identity authored/reviewed),
not a GitHub/CI technical restriction. That does not change what this owner
is authorized to do — self-merge stays out of scope regardless of the
technical merge path being open — but it gives the next human touchpoint a
one-line, pre-verified merge command
(`gh pr merge 173 --repo ajoe734/execute-plans --merge`) instead of having to
re-derive merge-safety from the CI run. See the sidecar packet for full
detail.

**Re-verification (2026-07-04, pass 18):** confirmed no state change since
the prior pass. `gh pr view 173 --repo ajoe734/execute-plans` shows the exact
same `headRefOid` (`2b054ab9f`), `OPEN`/`MERGEABLE`/`CLEAN`, zero reviews,
`autoMergeRequest=null`, and `integration-gate` `SUCCESS` at the same
timestamp (`2026-07-04T03:28:21Z`). `gh pr view 173 --json state,mergedAt,closedAt`
confirms it is still `OPEN` with `mergedAt=null`/`closedAt=null`. Pantheon-side
commit `7b360cb60` is still an ancestor of `origin/dev`
(`git merge-base --is-ancestor 7b360cb60 origin/dev`, current tip
`d3aff7280`). `orchestrator_approval_broker` MCP was searched again via
`ToolSearch` and is still not resolvable. No pantheon-side action is
available this pass; the sole remaining blocker is unchanged — a human/chair
merging `ajoe734/execute-plans#173`. Zero commits on the parent lane this
pass beyond this re-verification note.
