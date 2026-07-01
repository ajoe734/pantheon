# MGMT-LOAD-003 Sidecar BFF Handoff Followup 2

Task ID: `MGMT-LOAD-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`
Parent task: `MGMT-LOAD-003` - Frontend shell fanout reduction
Helper kind: `bff_handoff_packet`
Owner: Codex
Reviewer: Claude
Prepared: 2026-07-01
Mutates canonical truth: false

## Scope

This packet is support-only. It does not change canonical architecture,
runtime/BFF implementation, frontend source, registry behavior, governance
contracts, or the `MGMT-LOAD-003` acceptance criteria. It is a followup handoff
for the parent owner to absorb or discard while implementing the primary
execute-plans frontend work.

This packet complements the earlier sidecar handoff:

```text
support/sidecars/MGMT-LOAD-003/MGMT-LOAD-003-SIDECAR-BFF-HANDOFF.md
```

The useful delta is narrow:

- `MGMT-LOAD-001` and `MGMT-LOAD-002` are archived `done`.
- `MGMT-LOAD-003` is active `in_progress`, owner Claude, reviewer Codex.
- The needed BFF endpoint already exists; the remaining gap is frontend
  consumption, fallback timing, and route-load proof.
- The inspected execute-plans checkout still shows first-mount shell fanout in
  `TopBar` and `JobProgressDrawer`.

## Current Read Snapshot

| Surface | Current finding | Source |
|---|---|---|
| Dependency baseline | `MGMT-LOAD-001` archived the `/management/evidence` route-load baseline and BFF fanout baseline. | `AI_NAME=Codex ./scripts/ai-status.sh show MGMT-LOAD-001` |
| BFF dependency | `MGMT-LOAD-002` archived `GET /bff/management/shell-summary` and one canonical `GET /bff/jobs` handler. | `AI_NAME=Codex ./scripts/ai-status.sh show MGMT-LOAD-002`; `services/control-plane/bff/main.py`; `services/control-plane/bff/test_mgmt_load_002_shell_summary.py` |
| Parent state | `MGMT-LOAD-003` is `in_progress`, owner Claude, reviewer Codex. | `AI_NAME=Codex ./scripts/ai-status.sh show MGMT-LOAD-003` |
| Frontend checkout inspected | Read-only inspection from `/home/lupin/code/execute-plans`, branch `task/MGMT-GAP-008-detail-honesty-followup`, clean worktree. | `git -C /home/lupin/code/execute-plans status -sb` |
| TopBar | Still starts `useMe()`, full `lists.approvals()`, `lists.alerts()`, `lists.jobs()`, and immediate plus interval `probeLiveHealth()` on mount. | `/home/lupin/code/execute-plans/src/platform/components/TopBar.tsx` |
| Jobs drawer | Still starts `lists.jobs()` on mount, before the drawer is expanded. | `/home/lupin/code/execute-plans/src/platform/components/JobProgressDrawer.tsx` |
| Notification center | Full alerts/approvals/jobs/incidents hydration is already gated behind `open`. Preserve that behavior. | `/home/lupin/code/execute-plans/src/platform/components/NotificationCenter.tsx` |
| Path builders | The inspected `paths` export has management read builders but no shell-summary builder yet. | `/home/lupin/code/execute-plans/src/lib/bff-v1/paths.ts` |
| Route-load test | Current spec records fanout as warnings/annotations and never uses `networkidle`; it is not yet the final hard gate. | `/home/lupin/code/execute-plans/e2e/22-management-evidence-load.spec.ts` |

## BFF Contract Ready For Frontend Use

No new BFF route work is known for this sidecar. `MGMT-LOAD-002` provides the
contract the parent should consume:

```text
GET /bff/management/shell-summary
```

Expected useful fields:

```json
{
  "data": {
    "counts": {
      "pending_approvals": 1,
      "open_alerts": 3,
      "running_jobs": 2
    },
    "session": {
      "operator_id": "op-id",
      "operatorId": "op-id",
      "display_label": "Operator",
      "displayLabel": "Operator",
      "roles": ["admin"],
      "session_kind": "stub",
      "sessionKind": "stub",
      "state": "active",
      "fresh": true,
      "mfa_verified": true
    },
    "transport": {
      "bff_status": "ok",
      "service": "operator-bff",
      "api_version": "..."
    }
  },
  "meta": {
    "snapshot_at": "...",
    "surfaces": {
      "shell_summary": { "status": "ok" },
      "pending_approvals": { "status": "ok" },
      "open_alerts": { "status": "ok", "source": "bff_cheap_count" },
      "running_jobs": { "status": "ok" }
    }
  }
}
```

Important boundaries for frontend use:

- Treat `data.counts` as badge state only. It is not a replacement for detail
  list rows.
- Use `meta.surfaces.*.status` and `freshness` to distinguish live, degraded,
  stale, and unavailable counts.
- Do not convert an unavailable shell summary into `0` live approvals, alerts,
  or jobs.
- Do not immediately fall back to full `/bff/approvals`, `/bff/alerts`, or
  `/bff/jobs` list hydration before the route primary content is visible.
- Keep `/bff/events/stream` out of route-ready and request-budget logic because
  it is a long-lived SSE connection.

## Frontend Absorption Sequence

A low-risk parent implementation order:

1. Add a path builder, for example
   `paths.mgmtShellSummary() => "/bff/management/shell-summary"`.
2. Add a small typed client/hook for shell summary and export it through
   `src/lib/bff-v1/index.ts`.
3. In `TopBar`, replace first-mount full list reads with shell-summary counts:
   `pending_approvals -> approvals`, `open_alerts -> alerts`,
   `running_jobs -> jobs`.
4. Use shell-summary `session` for first-paint user chrome if it is sufficient.
   If `/bff/me` remains necessary, defer it until after the route primary
   milestone or make it the only extra pre-first-row read.
5. Use shell-summary `transport` for first-paint BFF status. Keep
   `probeLiveHealth()` only after primary route readiness, or prove it remains
   within the pre-first-row budget.
6. Move jobs list hydration into a shared jobs shell store with an in-flight
   request guard. `TopBar` should seed count state; `JobProgressDrawer` should
   hydrate full rows only when expanded or after primary content is visible.
7. Leave `NotificationCenter` hydration behind `open`; do not regress it into a
   mount-time fanout source.
8. Realtime count updates should only mutate live counts after a live shell
   summary or an explicitly hydrated list establishes a trustworthy base.

The key implementation trap is a "successful" shell-summary call followed by
unchanged mount-time full-list hydration. That would add a request instead of
reducing fanout.

## Operator Journey

### Direct Evidence Route Load

```text
operator opens /management/evidence
  -> shell and route mount
  -> primary route reads GET /bff/management/evidence
  -> shell reads GET /bff/management/shell-summary
  -> optional one extra session or health read only if the budget still holds
  -> first Evidence row or empty state becomes visible
  -> full approvals/alerts/jobs rows remain unfetched
```

Budget target before first row or empty state:

- primary route read: `/bff/management/evidence`;
- non-primary shell read: `/bff/management/shell-summary`;
- at most one additional non-primary bounded read;
- `/bff/events/stream` excluded as realtime SSE.

### Shell Summary Degraded Or Unavailable

```text
shell-summary returns degraded metadata, 401/403, 5xx, or transport failure
  -> badges render honest stale/degraded/unavailable state
  -> full list fallback is deferred until after primary route content
  -> user-opened surfaces may hydrate the single relevant list
```

This is an honesty requirement: missing summary data is not evidence that the
queues are empty.

### User Opens Jobs Or Notification Surface

```text
operator expands jobs drawer or opens notification center
  -> frontend hydrates that one list surface
  -> request is shared or guarded if another consumer already started it
  -> hydrated rows may refresh the shared shell count state
```

## Test And Proof Handoff

Recommended parent proof after implementation:

- Unit or component coverage for shell-summary success, degraded summary, and
  unavailable summary fallback in the TopBar layer.
- A jobs drawer test proving `lists.jobs()` is not called on mount and is called
  once on expand/open or post-primary hydration.
- Route-load e2e hard assertions after the parent fix:
  - non-primary BFF requests before first row or empty state are `<= 2`;
  - `/bff/jobs` request count before first row or empty state is not duplicated;
  - shell-summary success does not trigger full approvals/alerts/jobs lists;
  - shell-summary degraded/unavailable state does not trigger immediate full-list
    fallback;
  - readiness uses `domcontentloaded` plus route heading/API/row or empty-state
    milestones, never `networkidle`.
- Hosted dev evidence should cite the MGMT-LOAD-001 baseline or successor gate
  output after the frontend branch is deployed.

## Parent Review Checklist

Before `MGMT-LOAD-003` returns for Codex review, Claude should be able to show:

- `TopBar` has no mount-time full-list fanout for approvals, alerts, or jobs.
- `/bff/management/shell-summary` is the shell badge/session/transport source
  for first paint.
- Degraded/unavailable shell summary renders honest non-live badge state.
- `/bff/me` and `/health` do not both run before first row unless measured
  request count remains inside the `<= 2` non-primary budget.
- `JobProgressDrawer` does not fetch `/bff/jobs` merely because
  `PlatformShell` mounted it.
- Notification center list hydration remains open-gated.
- Route-load tests no longer treat the MGMT-LOAD-003 request budget as only a
  warning.

## Verification For This Sidecar

This sidecar changed no runtime or frontend code. Verification was read-only
source and artifact inspection:

```bash
git status -sb
git branch --show-current
git remote -v
AI_NAME=Codex ./scripts/ai-status.sh show MGMT-LOAD-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-2
AI_NAME=Codex ./scripts/ai-status.sh show MGMT-LOAD-003
AI_NAME=Codex ./scripts/ai-status.sh show MGMT-LOAD-001
AI_NAME=Codex ./scripts/ai-status.sh show MGMT-LOAD-002
sed -n '1,260p' docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-003-fe-shell-fanout.md
sed -n '1,260p' docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-002-bff-shell-summary.md
sed -n '1,220p' docs/04/pantheon_management_console_load_gap_2026-07-01/archive/route-load-baseline-2026-07-01.md
sed -n '1,240p' docs/04/pantheon_management_console_load_gap_2026-07-01/archive/bff-fanout-baseline-2026-07-01.md
sed -n '1,180p' /home/lupin/code/execute-plans/src/platform/components/TopBar.tsx
sed -n '1,150p' /home/lupin/code/execute-plans/src/platform/components/JobProgressDrawer.tsx
sed -n '1,220p' /home/lupin/code/execute-plans/src/platform/components/NotificationCenter.tsx
sed -n '1,230p' /home/lupin/code/execute-plans/e2e/22-management-evidence-load.spec.ts
sed -n '1,180p' /home/lupin/code/execute-plans/src/lib/bff-v1/paths.ts
sed -n '14900,15240p' services/control-plane/bff/main.py
sed -n '90,175p' services/control-plane/bff/test_mgmt_load_002_shell_summary.py
```

## Reviewer Handoff

Claude should review this packet as support material only:

1. Confirm it does not edit canonical truth or implementation surfaces.
2. Confirm the BFF contract summary matches the merged `MGMT-LOAD-002` route.
3. Confirm the frontend seam findings match the current execute-plans checkout
   before absorbing them into the parent task.
4. Use the parent `MGMT-LOAD-003` execution packet and reviewer acceptance as
   the controlling work scope.
