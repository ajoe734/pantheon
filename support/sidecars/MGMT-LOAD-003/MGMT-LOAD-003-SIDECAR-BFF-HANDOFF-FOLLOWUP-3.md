# MGMT-LOAD-003 Sidecar BFF Handoff Followup 3

Task ID: `MGMT-LOAD-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-3`
Parent task: `MGMT-LOAD-003` - Frontend shell fanout reduction
Helper kind: `bff_handoff_packet`
Owner: Codex
Reviewer: Claude
Prepared: 2026-07-01
Mutates canonical truth: false

## Scope

This is a support-only packet for the parent owner. It does not change L1
canonical truth, BFF runtime behavior, execute-plans frontend source, route
registries, governance contracts, or parent acceptance. It updates the
`MGMT-LOAD-003` handoff with a fresh read-only snapshot and a concrete
absorption recipe for the frontend shell fanout fix.

This packet complements:

```text
support/sidecars/MGMT-LOAD-003/MGMT-LOAD-003-SIDECAR-BFF-HANDOFF.md
support/sidecars/MGMT-LOAD-003/MGMT-LOAD-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md
```

## Current Coordination Snapshot

| Surface | Finding |
|---|---|
| Parent state | `MGMT-LOAD-003` is active `in_progress`, owner Claude, reviewer Codex. |
| Dependency baseline | `MGMT-LOAD-001` is archived `done`; route-load and BFF fanout baselines are durable. |
| BFF dependency | `MGMT-LOAD-002` is archived `done`; `/bff/management/shell-summary` and the single canonical `/bff/jobs` route are merged to `dev`. |
| This sidecar | Created because Copilot quota-terminaled; Codex owns this support packet and Claude reviews it. |
| Allowed mutation | Support artifact and task coordination only. Parent implementation remains in execute-plans under Claude. |

## Fresh Read-Only Frontend Snapshot

The inspected execute-plans checkout was:

```text
/home/lupin/code/execute-plans
branch: task/MGMT-GAP-008-detail-honesty-followup
status: clean against origin/task/MGMT-GAP-008-detail-honesty-followup
```

This is not the parent `MGMT-LOAD-003` implementation branch. Claude should
re-check the parent checkout before applying any item below.

| File | Current finding | Parent implication |
|---|---|---|
| `src/platform/components/TopBar.tsx` | Still calls `useMe()`, `Promise.all([lists.approvals(), lists.alerts(), lists.jobs()])`, and immediate plus interval `probeLiveHealth()` on mount. | Still violates the intended first-route request budget until shell-summary replaces the full-list reads and first-paint session/transport source. |
| `src/platform/components/JobProgressDrawer.tsx` | Still calls `lists.jobs()` in a mount effect before the drawer is expanded. | Still duplicates `/bff/jobs` with TopBar before the route primary content milestone. |
| `src/platform/components/NotificationCenter.tsx` | Full alerts/approvals/jobs/incidents hydration remains gated behind `open`. | Preserve this behavior; do not move it back to shell mount. |
| `src/lib/bff-v1/paths.ts` | No `mgmtShellSummary` or equivalent path builder exists. | Parent needs to add one for `/bff/management/shell-summary`. |
| `src/lib/bff-v1/index.ts` | No shell-summary helper is exported. | Parent should export a small typed helper/store if added under `src/lib/bff-v1`. |
| `e2e/22-management-evidence-load.spec.ts` | Still records fanout as warnings/annotations; it does not hard gate the MGMT-LOAD-003 budget. | After the frontend fix, convert the relevant budget assertions into gates. |

No frontend shell-summary references were found under `src` or `e2e`.

## BFF Contract Delta Relevant To Frontend

`MGMT-LOAD-002` already supplies the BFF route. Current implementation details
that matter for frontend consumption:

```text
GET /bff/management/shell-summary
```

- Requires normal read role/session checks.
- Returns `data.counts.pending_approvals`, `data.counts.open_alerts`, and
  `data.counts.running_jobs`.
- Returns redacted first-paint `data.session` and `data.transport`.
- Returns `meta.surfaces.shell_summary`, `pending_approvals`, `open_alerts`,
  and `running_jobs` with status/freshness/degraded metadata.
- Uses cheap count sources and count caching; it does not return full list
  payloads and must not be treated as a row source.

Important frontend boundary:

Missing, degraded, or unavailable shell summary is not evidence that approvals,
alerts, or jobs are empty. The UI should show honest non-live shell state and
defer full list reads until after primary route content or explicit user action.

## Suggested Parent Implementation Recipe

1. Add a path builder:

   ```ts
   mgmtShellSummary: () => `${BASE}/management/shell-summary`,
   ```

2. Add a narrow shell-summary helper instead of extending `lists`:

   - place it near `src/lib/bff-v1/management.ts` or in
     `src/lib/bff-v1/shellSummary.ts`;
   - export it from `src/lib/bff-v1/index.ts`;
   - return a discriminated state, not raw counts only.

   Suggested state shape:

   ```ts
   type ShellSummaryStatus =
     | "loading"
     | "live"
     | "degraded"
     | "unavailable"
     | "mock"
     | "fallback";

   interface ShellSummaryView {
     status: ShellSummaryStatus;
     counts: {
       approvals?: number;
       alerts?: number;
       jobs?: number;
     };
     countsAreLive: boolean;
     session?: {
       operatorId?: string;
       displayLabel?: string;
       roles: string[];
       state?: string;
       fresh?: boolean;
       mfaVerified?: boolean;
     };
     transport?: {
       bffStatus?: string;
       service?: string;
       apiVersion?: string;
     };
     meta?: Record<string, unknown>;
     reason?: string;
   }
   ```

   The key is `countsAreLive`: it should only be true when the shell-summary
   surface and the relevant count surface are live/ok/fresh enough. Degraded
   counts may be displayed with degraded copy, but should not be silently
   treated as live truth.

3. In `TopBar`, replace mount-time full-list reads with the helper:

   - remove first-mount `lists.approvals()`, `lists.alerts()`, and
     `lists.jobs()`;
   - map `pending_approvals -> approvals`, `open_alerts -> alerts`,
     `running_jobs -> jobs`;
   - use shell-summary session for first-paint user chrome when sufficient;
   - defer `/bff/me` if richer session detail is still needed;
   - use shell-summary transport for first-paint BFF status;
   - defer `probeLiveHealth()` until after primary content or prove it remains
     the only extra non-primary read before first row/empty state.

4. Move jobs state into a shared shell jobs store:

   - seed only `runningCount` from shell summary;
   - keep `rows`, `hydrated`, and `inFlight` separately;
   - do not call `lists.jobs()` in `JobProgressDrawer` mount;
   - hydrate full job rows on drawer expand/open or after primary content;
   - guard duplicate hydration with a shared in-flight promise;
   - allow realtime job events to update the count only after a live shell
     summary or explicit jobs hydration establishes a trustworthy base.

5. Preserve NotificationCenter behavior:

   - it may hydrate full lists when `open === true`;
   - it should not be used as the TopBar count source before first route
     content.

## Operator Journey To Preserve

Direct `/management/evidence` route load:

```text
operator opens /management/evidence
  -> shell and route mount
  -> primary route reads GET /bff/management/evidence
  -> shell reads GET /bff/management/shell-summary
  -> at most one additional bounded non-primary read before first row/empty state
  -> first Evidence row or empty state is visible
  -> full approvals/alerts/jobs rows are still unfetched
```

Shell summary degraded/unavailable:

```text
shell-summary is degraded, unavailable, 401/403, 5xx, or transport-failed
  -> badges show checking/degraded/unavailable/fallback state honestly
  -> no immediate full-list fallback runs before primary content
  -> user-opened surfaces may hydrate the single relevant list
```

Jobs drawer:

```text
shell summary says running_jobs = N
  -> TopBar can show N as live/degraded according to surfaces
  -> drawer does not fetch rows on mount
  -> expanding drawer hydrates /bff/jobs once, then shares rows/cache
```

## Test And Proof Gates For Parent

After Claude lands the frontend change, the parent task should be able to show:

- Component or hook tests for shell-summary success, degraded summary, and
  unavailable/transport failure.
- A TopBar test proving shell-summary success does not call full
  approvals/alerts/jobs list readers on mount.
- A TopBar fallback test proving shell-summary failure does not immediately
  call the full list readers before route primary content.
- A JobProgressDrawer test proving mount does not call `lists.jobs()` and
  expand/open calls it once.
- An e2e route-load gate for `/management/evidence`:
  - exclude `/bff/events/stream`;
  - do not use Playwright `networkidle`;
  - assert non-primary BFF requests before first row/empty state are `<= 2`;
  - assert `/bff/jobs` is not duplicated before first row/empty state;
  - assert shell-summary success does not trigger full list fanout;
  - assert degraded/unavailable summary does not trigger pre-primary full-list
    fallback.

Recommended e2e fixture additions:

```text
/bff/management/shell-summary -> success with counts
/bff/management/shell-summary -> 200 degraded surfaces
/bff/management/shell-summary -> 503 or transport failure
/bff/approvals, /bff/alerts, /bff/jobs -> counters that fail if hit before first row
```

## Parent Absorption Checklist

Claude should treat these as support checks, not as new acceptance:

- `paths.ts` exposes `/bff/management/shell-summary`.
- TopBar has no mount-time full-list reads for approvals, alerts, or jobs.
- TopBar no longer needs both `/bff/me` and `/health` before the primary route
  milestone unless measured non-primary count still stays `<= 2`.
- Shell-summary degraded/unavailable state is visible and not rendered as live
  zero counts.
- JobProgressDrawer does not hydrate `/bff/jobs` during shell mount.
- Jobs hydration is shared/guarded, so drawer expand and jobs page cannot start
  duplicate pre-primary reads.
- NotificationCenter remains open-gated.
- Route-load proof is a hard gate, not only a console warning.

## Verification For This Sidecar

This sidecar changed no runtime or frontend implementation. Verification was
read-only source/status inspection plus this support artifact creation:

```bash
git status -sb
git branch --show-current
git remote -v
AI_NAME=Codex ./scripts/ai-status.sh show MGMT-LOAD-003
AI_NAME=Codex ./scripts/ai-status.sh show MGMT-LOAD-001
AI_NAME=Codex ./scripts/ai-status.sh show MGMT-LOAD-002
git -C /home/lupin/code/execute-plans status -sb
git -C /home/lupin/code/execute-plans branch --show-current
rg -n "shell-summary|shellSummary|mgmtShellSummary|management/shell-summary" \
  /home/lupin/code/execute-plans/src /home/lupin/code/execute-plans/e2e
sed -n '1,280p' /home/lupin/code/execute-plans/src/platform/components/TopBar.tsx
sed -n '1,240p' /home/lupin/code/execute-plans/src/platform/components/JobProgressDrawer.tsx
sed -n '1,280p' /home/lupin/code/execute-plans/src/platform/components/NotificationCenter.tsx
sed -n '1,260p' /home/lupin/code/execute-plans/src/lib/bff-v1/paths.ts
sed -n '1,260p' /home/lupin/code/execute-plans/e2e/22-management-evidence-load.spec.ts
sed -n '14880,15280p' services/control-plane/bff/main.py
sed -n '80,190p' services/control-plane/bff/test_mgmt_load_002_shell_summary.py
```

## Reviewer Handoff

Claude should review this packet as support material only:

1. Confirm it stays within sidecar scope.
2. Confirm the BFF contract notes still match `MGMT-LOAD-002` on current `dev`.
3. Re-check execute-plans on the actual parent implementation branch before
   absorbing any item.
4. Use the parent `MGMT-LOAD-003` task and acceptance as the controlling scope.
