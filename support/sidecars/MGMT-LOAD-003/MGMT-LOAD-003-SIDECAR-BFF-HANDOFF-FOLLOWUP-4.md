# MGMT-LOAD-003 Sidecar BFF Handoff Followup 4

Task ID: `MGMT-LOAD-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-4`
Parent task: `MGMT-LOAD-003` - Frontend shell fanout reduction
Helper kind: `bff_handoff_packet`
Owner: Codex
Reviewer: Claude
Prepared: 2026-07-01
Mutates canonical truth: false

## Scope

This is a support-only sidecar for the parent owner and reviewer. It does not
change L1 canonical truth, BFF runtime code, execute-plans frontend source,
route registries, governance contracts, or the parent task acceptance.

Unlike followup-2 and followup-3, this packet does not repeat the original
implementation recipe. The parent execute-plans branch has already implemented
most of that recipe. This followup packages the current read-only branch
snapshot and calls out the remaining acceptance proof risks that should be
checked before `MGMT-LOAD-003` returns for final review.

This packet complements:

```text
support/sidecars/MGMT-LOAD-003/MGMT-LOAD-003-SIDECAR-BFF-HANDOFF.md
support/sidecars/MGMT-LOAD-003/MGMT-LOAD-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md
support/sidecars/MGMT-LOAD-003/MGMT-LOAD-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md
```

## Current Coordination Snapshot

| Surface | Finding |
|---|---|
| Parent state | `MGMT-LOAD-003` is active `in_progress`, owner Claude, reviewer Codex. |
| Dependencies | `MGMT-LOAD-001` and `MGMT-LOAD-002` are archived `done`. |
| BFF contract | Pantheon `dev` includes `GET /bff/management/shell-summary` and one canonical `GET /bff/jobs` route from `MGMT-LOAD-002`. |
| Parent frontend branch inspected | `execute-plans` `origin/task/MGMT-LOAD-003` at `b0f317a588ef3498be7479efff6c51b82bee84cd`. |
| Parent frontend current local checkout | `/home/lupin/code/execute-plans` is on `task/MGMT-GAP-008-detail-honesty-followup`; this sidecar inspected the parent branch through `git show origin/task/MGMT-LOAD-003:<path>` and did not switch or edit it. |
| Pantheon sidecar base | This worktree is `task/MGMT-LOAD-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` at Pantheon `origin/dev` `8cf8eee48178e3efcbd8646f4ced1772783a403b` before this packet. |

## What The Parent Branch Already Has

The parent branch has moved beyond the older followup recipe.

| Area | Evidence on `origin/task/MGMT-LOAD-003` | Reviewer implication |
|---|---|---|
| Path builder | `src/lib/bff-v1/paths.ts` has `mgmtShellSummary: () => "/bff/management/shell-summary"`. | Do not ask parent to add the route builder again. |
| Shell summary client | `src/lib/bff-v1/shellSummary.ts` adapts counts, session, transport, and `meta.surfaces`, and is exported from `src/lib/bff-v1/index.ts`. | Review the adapter behavior, not the absence of an adapter. |
| TopBar healthy/degraded path | `src/platform/components/TopBar.tsx` calls `fetchShellSummary()` and no longer uses full approvals/alerts/jobs lists for the healthy or degraded shell-summary path. | The old first-mount full-list fanout gap is mostly addressed. |
| TopBar unavailable path | Full-list fallback is scheduled through `scheduleIdleTask()` only after shell-summary is unavailable or unknown. | Verify this is late enough for the route-ready budget; see residual risks below. |
| Jobs drawer | `src/platform/components/JobProgressDrawer.tsx` no longer calls `lists.jobs()` synchronously on mount and guards hydration with `hydrated`. | Duplicate jobs reads are reduced, but timing still needs budget proof. |
| Notification center | `src/platform/components/NotificationCenter.tsx` still gates full list hydration behind `open`. | Preserve this behavior. |
| Unit coverage | `TopBar.test.tsx`, `JobProgressDrawer.test.tsx`, and `shellSummary.test.ts` cover shell-summary success, degraded/unavailable fallback, and lazy jobs hydration. | Good focused coverage exists. |
| E2E coverage | `e2e/23-management-shell-fanout.spec.ts` was added by `b0f317a`. | Useful, but it does not yet prove the full request-budget acceptance. |

Relevant parent commits:

```text
f6b973d MGMT-LOAD-003: shell consumes summary, defers full-list fanout
b0f317a MGMT-LOAD-003: fix shell-summary liveStatus leak, add e2e coverage
```

## BFF Contract Recheck

The BFF side is still ready for frontend consumption.

```text
GET /bff/management/shell-summary
```

The Pantheon implementation returns:

- `data.counts.pending_approvals`
- `data.counts.open_alerts`
- `data.counts.running_jobs`
- redacted first-paint `data.session`
- first-paint `data.transport`
- `meta.surfaces.shell_summary`
- per-count surfaces for `pending_approvals`, `open_alerts`, and
  `running_jobs`

`services/control-plane/bff/test_mgmt_load_002_shell_summary.py` still verifies
that shell summary does not call the full alert payload builder, does not
return full approvals/alerts/jobs list payloads, exposes redacted session and
transport data, surfaces degraded count state, registers OpenAPI, and has one
canonical `GET /bff/jobs` handler.

No BFF implementation gap is known from this sidecar read.

## Residual Acceptance Risks For Parent

These are not sidecar implementation requests. They are the concrete places
Claude and the parent reviewer should inspect before claiming the parent task
meets acceptance.

### 1. The total pre-first-row budget is not hard-gated yet

Parent acceptance says `/management/evidence` should start no more than two
non-primary BFF requests before first row or empty state.

The current parent branch still has these immediate shell reads in `TopBar`:

```text
src/platform/components/TopBar.tsx:35  useMe()
src/platform/components/TopBar.tsx:90  fetchShellSummary()
src/platform/components/TopBar.tsx:137 probeLiveHealth()
```

That can be three non-primary reads before the Evidence route's first row:

```text
/bff/me
/bff/management/shell-summary
/health
```

The parent e2e coverage sees these routes, but does not hard-fail on the total
budget:

- `e2e/22-management-evidence-load.spec.ts` records
  `nonPrimaryRequestsBeforeFirstRow`, but still annotates/warns.
- `e2e/23-management-shell-fanout.spec.ts` asserts shell-summary success,
  degraded/unavailable behavior, and no full-list fanout, but does not assert
  `nonPrimaryRequestsBeforeFirstRow <= 2`.

Recommended parent action:

- either derive first-paint session/transport from shell summary and defer
  `/bff/me` or `/health`;
- or keep one of `/bff/me` or `/health`, not both, before first row;
- and add a hard e2e assertion that counts all non-primary BFF/health requests
  started before the first row or empty state, excluding the long-lived
  `/bff/events/stream`.

### 2. Jobs drawer idle hydration is not tied to primary-content readiness

`JobProgressDrawer` no longer fetches jobs synchronously on mount, but it does
schedule idle hydration from mount:

```text
src/platform/components/JobProgressDrawer.tsx:83 scheduleIdleTask(hydrate)
src/lib/idleTask.ts:9 requestIdleCallback or a 1200 ms timeout fallback
```

On a fast route this may be acceptable. On a slow primary Evidence read, a
browser idle callback or the 1200 ms fallback can still start `/bff/jobs`
before the first row or empty state. The current e2e accepts `jobs <= 1`, but
the parent acceptance is about pre-first-row fanout budget and duplicate jobs
before first row.

Recommended parent action:

- make jobs hydration explicitly user-opened or primary-content-gated; or
- extend the e2e fixture with a delayed primary Evidence response and prove
  `/bff/jobs` does not start before the route milestone unless the user opens
  the drawer.

### 3. The new e2e gate is narrower than the acceptance sentence

`e2e/23-management-shell-fanout.spec.ts` is useful and CI-safe, but its current
hard checks are narrower than the task acceptance:

- it proves TopBar does not fall back to full approvals/alerts/jobs on shell
  summary success;
- it proves degraded summary reaches first row without full-list fanout;
- it proves unavailable summary full-list fallback is deferred relative to the
  immediate route sequence;
- it allows one `/bff/jobs` read through the drawer idle hydration;
- it does not hard-check total non-primary request count at the first-row
  milestone.

Recommended parent action:

- keep `e2e/23` as the parent task's hard gate, but add a request log with
  start timestamps and assert the exact budget at first row or empty state:

```text
nonPrimaryBeforeFirstRow <= 2
jobsBeforeFirstRow <= 1
approvalsBeforeFirstRow == 0 when shell-summary is ok/degraded
alertsBeforeFirstRow == 0 when shell-summary is ok/degraded
```

If the intended parent interpretation allows `/bff/me`, `/health`, and
shell-summary all before first row, update the acceptance wording through the
parent task rather than hiding the mismatch in test comments.

## Operator Journey To Preserve

Desired direct `/management/evidence` route load after parent closure:

```text
operator opens /management/evidence
  -> shell and route mount
  -> primary route reads GET /bff/management/evidence
  -> shell reads GET /bff/management/shell-summary
  -> at most one additional bounded non-primary read starts before first row
  -> first Evidence row or empty state is visible
  -> full approvals/alerts/jobs lists remain unfetched before that milestone
  -> jobs drawer hydrates only after primary content or explicit open
```

Shell summary unavailable:

```text
shell-summary returns unavailable, unknown, 401/403, 5xx, or transport failure
  -> TopBar shows honest unavailable/fallback shell state
  -> full-list fallback does not race first row
  -> user-opened surfaces may hydrate one relevant list
```

## Parent Review Checklist

Before `MGMT-LOAD-003` returns for review, Claude should be able to show:

- the parent branch is still based on the current execute-plans delivery base;
- `TopBar` uses shell-summary for badge counts and does not read full
  approvals/alerts/jobs lists on healthy or degraded shell-summary;
- `/bff/me` and `/health` do not both start before first row unless the measured
  total non-primary budget still passes `<= 2`;
- shell-summary degraded/unavailable state is visible and not rendered as live
  zero counts;
- `JobProgressDrawer` does not start `/bff/jobs` before first row unless the
  user opens the drawer, or the parent explicitly documents and tests the
  permitted idle-hydration timing;
- `NotificationCenter` remains open-gated;
- `e2e/23-management-shell-fanout.spec.ts` or a successor gate hard-fails the
  exact parent acceptance budget, not only full-list fanout.

## Verification For This Sidecar

This sidecar changed no runtime or frontend implementation. Verification was
read-only source/status inspection plus support artifact creation:

```bash
git status -sb
git branch --show-current
git remote -v
AI_NAME=Codex ./scripts/ai-status.sh show MGMT-LOAD-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-4
AI_NAME=Codex ./scripts/ai-status.sh show MGMT-LOAD-003
AI_NAME=Codex ./scripts/ai-status.sh show MGMT-LOAD-001
AI_NAME=Codex ./scripts/ai-status.sh show MGMT-LOAD-002
AI_NAME=Codex ./scripts/ai-status.sh show MGMT-LOAD-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-3
git -C /home/lupin/code/execute-plans fetch origin main task/MGMT-LOAD-003
git -C /home/lupin/code/execute-plans log --oneline --decorate -8 origin/task/MGMT-LOAD-003 --
git -C /home/lupin/code/execute-plans show origin/task/MGMT-LOAD-003:src/lib/bff-v1/paths.ts
git -C /home/lupin/code/execute-plans show origin/task/MGMT-LOAD-003:src/lib/bff-v1/shellSummary.ts
git -C /home/lupin/code/execute-plans show origin/task/MGMT-LOAD-003:src/platform/components/TopBar.tsx
git -C /home/lupin/code/execute-plans show origin/task/MGMT-LOAD-003:src/platform/components/JobProgressDrawer.tsx
git -C /home/lupin/code/execute-plans show origin/task/MGMT-LOAD-003:src/platform/components/NotificationCenter.tsx
git -C /home/lupin/code/execute-plans show origin/task/MGMT-LOAD-003:e2e/22-management-evidence-load.spec.ts
git -C /home/lupin/code/execute-plans show origin/task/MGMT-LOAD-003:e2e/23-management-shell-fanout.spec.ts
git -C /home/lupin/code/execute-plans grep -n "toBeLessThanOrEqual(2\\|nonPrimaryRequestsBeforeFirstRow\\|beforeFirstRow\\|firstRow" origin/task/MGMT-LOAD-003 -- e2e src/platform/components src/lib/bff-v1
git -C /home/lupin/code/execute-plans grep -n "useMe()\\|probeLiveHealth\\|fetchShellSummary\\|lists\\.approvals\\|lists\\.alerts\\|lists\\.jobs\\|scheduleIdleTask" origin/task/MGMT-LOAD-003 -- src/platform/components/TopBar.tsx src/platform/components/JobProgressDrawer.tsx src/lib/idleTask.ts
sed -n '14880,15280p' services/control-plane/bff/main.py
sed -n '80,200p' services/control-plane/bff/test_mgmt_load_002_shell_summary.py
```

No frontend tests were run from this Pantheon sidecar worktree because the
sidecar is read-only with respect to execute-plans. The parent branch should run
the focused Vitest and Playwright gates before it returns for review.

## Reviewer Handoff

Claude should review this packet as support material only:

1. Confirm it stays within sidecar scope.
2. Confirm the BFF contract notes still match `MGMT-LOAD-002` on Pantheon
   `dev`.
3. Confirm the parent branch findings match the current execute-plans
   `origin/task/MGMT-LOAD-003` before absorbing them.
4. Treat the residual acceptance risks as parent review checks, especially the
   total `nonPrimaryBeforeFirstRow <= 2` hard gate.

## Closeout Review Note

Reviewer approval confirmed this followup-4 packet stays within support-only
sidecar scope. The BFF contract and parent-branch references were checked
against `services/control-plane/bff/main.py` and execute-plans
`origin/task/MGMT-LOAD-003` at `b0f317a`.

The remaining request-budget and jobs-hydration concerns are parent
`MGMT-LOAD-003` review checks, not open work for this sidecar.
