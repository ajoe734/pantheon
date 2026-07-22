# MGMT-LOAD-003 Sidecar BFF and Frontend Handoff Packet

Task ID: `MGMT-LOAD-003-SIDECAR-BFF-HANDOFF`
Parent task: `MGMT-LOAD-003` - Frontend shell fanout reduction
Helper kind: `bff_handoff_packet`
Owner: Codex
Reviewer: Claude
Prepared: 2026-07-01
Mutates canonical truth: false

## Scope

This is a support-only sidecar for `MGMT-LOAD-003`. It does not define L1
canonical truth, change BFF runtime behavior, change frontend implementation, or
alter route/registry/governance contracts. It packages the current BFF shell
summary contract, the observed frontend fanout seams, the operator journey, and
the parent-owner absorption checklist for Claude to use or discard while
implementing the primary task.

Current parent state at packet time:

| Area | State |
|---|---|
| Parent task | `MGMT-LOAD-003` is `in_progress`, owner Claude, reviewer Codex. |
| Parent acceptance | `/management/evidence` starts no more than two non-primary BFF requests before first row or empty state; no duplicate `/bff/jobs` before that milestone; tests cover summary success, degraded summary, unavailable fallback, and lazy drawer hydration. |
| Dependency baseline | `MGMT-LOAD-001` archived the route-load and BFF fanout baseline. |
| BFF dependency | `MGMT-LOAD-002` is done: `/bff/management/shell-summary` and single canonical `/bff/jobs` are merged to dev. |
| Related lanes | `MGMT-LOAD-004` owns route code splitting; `MGMT-LOAD-005` owns BFF read concurrency isolation; `MGMT-LOAD-006/007` own release-gate and final hosted proof. |

## Source Snapshot

| Surface | Current observed state | Source |
|---|---|---|
| Slow-route baseline | First hosted baseline saw 11 non-primary BFF/FE requests before first row and two `/bff/jobs` reads before first row. | `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/route-load-baseline-2026-07-01.md` |
| BFF fanout baseline | Concurrent `/health`, Evidence, alerts, approvals, and jobs reads had p95 values from 1328 ms to 1538 ms. | `docs/04/pantheon_management_console_load_gap_2026-07-01/archive/bff-fanout-baseline-2026-07-01.md` |
| Shell summary route | `GET /bff/management/shell-summary` returns cheap counts, redacted session, transport, and freshness/degraded surfaces without full list payloads. | `services/control-plane/bff/main.py`; `services/control-plane/bff/test_mgmt_load_002_shell_summary.py` |
| Jobs route | Source and registered route now have one canonical `GET /bff/jobs` handler. | `services/control-plane/bff/test_mgmt_load_002_shell_summary.py` |
| TopBar fanout | `TopBar` still calls `useMe()`, full `lists.approvals()`, `lists.alerts()`, `lists.jobs()`, and immediate/interval `probeLiveHealth()`. | `/home/lupin/code/execute-plans/src/platform/components/TopBar.tsx` |
| Duplicate jobs hydration | `JobProgressDrawer` still calls `lists.jobs()` during mount, independently of `TopBar`. | `/home/lupin/code/execute-plans/src/platform/components/JobProgressDrawer.tsx` |
| Shared shell SSE | `PlatformShell` opens `/bff/events/stream`; route readiness must not use `networkidle`. | `/home/lupin/code/execute-plans/src/platform/PlatformShell.tsx`; `/home/lupin/code/execute-plans/e2e/22-management-evidence-load.spec.ts` |
| Frontend path builders | `paths` has Evidence and core list paths, but no shell-summary path builder in the inspected checkout. | `/home/lupin/code/execute-plans/src/lib/bff-v1/paths.ts` |

Frontend source was inspected read-only from
`/home/lupin/code/execute-plans`, currently on
`task/MGMT-GAP-008-detail-honesty-followup`. This packet does not approve,
reject, or modify that checkout.

## BFF Contract Handoff

`MGMT-LOAD-002` already supplies the BFF contract needed by
`MGMT-LOAD-003`.

Route:

```text
GET /bff/management/shell-summary
```

Important response fields:

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
      "roles": ["operator"],
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

BFF behavior to preserve from the frontend:

- Do not treat shell summary as a replacement for the full approvals, alerts,
  or jobs list pages. It is a shell badge/session/transport summary only.
- Do not infer list rows from counts. Counts are badge data, not list data.
- Use `meta.surfaces.*.status`, freshness, and degraded metadata to decide
  whether a count is live, stale, degraded, or unavailable.
- If shell summary is unavailable or a surface is degraded, show honest shell
  state and defer full list reads until after route primary content is visible
  or the user opens the relevant surface.
- Keep `/bff/events/stream` out of readiness calculations; it is expected to be
  long-lived.

## BFF Query Gap Matrix

| Frontend need | Current BFF route | Current frontend state | Required parent action |
|---|---|---|---|
| TopBar badge counts | `GET /bff/management/shell-summary` | Counts are derived from full `/bff/approvals`, `/bff/alerts`, `/bff/jobs` payloads. | Add a shell-summary client/path and make `TopBar` use `data.counts`. |
| TopBar session chrome | `data.session` in shell summary, or existing `/bff/me` if full session is needed later | `useMe()` starts `/bff/me` on first mount. | Prefer shell-summary session for first paint. If `/bff/me` remains necessary, defer it or keep total non-primary requests within the budget. |
| TopBar transport status | `data.transport` and `meta.surfaces.shell_summary` | `probeLiveHealth()` starts immediately and repeats every 30 s. | Use shell summary transport for first paint; keep health probing only if it does not exceed the pre-first-row budget, or move it after primary route readiness. |
| Approvals/alerts/jobs detail lists | Existing `/bff/approvals`, `/bff/alerts`, `/bff/jobs` | `TopBar` fetches all three full lists on mount. | Fetch full lists only on explicit navigation/open or after primary content readiness. |
| Running jobs drawer | `GET /bff/jobs` for full drawer hydration; `data.counts.running_jobs` for shell count | `JobProgressDrawer` fetches `/bff/jobs` on mount, duplicating TopBar. | Render from shell-summary count/realtime events first; hydrate full jobs only when expanded or after primary content. |
| E2E route readiness | Primary Evidence API plus heading/row/empty-state milestones | Existing baseline spec warns on fanout but does not hard gate it. | Add hard assertions for non-primary request count and duplicate jobs once the parent fix lands. |

There is no known BFF route implementation gap for `MGMT-LOAD-003` after
`MGMT-LOAD-002`. Remaining work is frontend consumption, fallback behavior, and
test/probe enforcement.

## Operator Journey

### Direct Evidence route load

```text
Operator opens /management/evidence
  -> FE loads the route chunk and mounts the shell
  -> primary route requests GET /bff/management/evidence
  -> shell may request GET /bff/management/shell-summary
  -> optional health/session follow-up must not push non-primary requests over 2
  -> first Evidence row or empty state becomes visible
  -> full approvals/alerts/jobs lists are still not fetched unless requested
```

Expected pre-first-row bounded requests:

- primary route read: `/bff/management/evidence`;
- non-primary shell read: `/bff/management/shell-summary`;
- at most one additional non-primary request if the parent owner keeps a
  separate health/session probe before primary content.

`/bff/events/stream` may open for live SSE, but it must be excluded from route
ready and network-idle logic.

### Shell summary degraded or unavailable

```text
Shell summary returns degraded metadata, 401/403, 5xx, or transport failure
  -> TopBar shows count badges as stale/degraded/unavailable, not zero-live
  -> no immediate full-list fallback runs before primary route content
  -> after primary content, an idle callback or explicit user action may fetch
     the relevant full list
```

The important distinction is count honesty: unavailable summary is not proof
that approvals, alerts, or jobs are empty.

### User opens a global surface

```text
Operator clicks approvals, alerts, jobs, notification center, or expands jobs
drawer
  -> FE hydrates that single full list
  -> hydrated list can update the shared shell store
  -> repeated opens reuse cache or share in-flight request
```

The drawer/list interaction is the correct time to pay the full list payload
cost. First-route Evidence paint is not.

## Frontend Handoff Notes

Suggested implementation shape for the parent owner:

1. Add a path builder, for example
   `paths.mgmtShellSummary() => "/bff/management/shell-summary"`.
2. Add a typed client/hook for shell summary. Either place it near
   `src/lib/bff-v1/management.ts` or as a small dedicated
   `src/lib/bff-v1/shellSummary.ts` exported through `src/lib/bff-v1/index.ts`.
3. In `TopBar`, replace the first-mount `Promise.all([lists.approvals(),
   lists.alerts(), lists.jobs()])` with shell-summary counts.
4. Use shell-summary `session` for the initial user menu where possible. If
   `useMe()` is still needed for richer session fields, start it after the
   primary route milestone or make sure it is the only extra pre-first-row read.
5. Treat `probeLiveHealth()` as post-primary or redundant with
   `data.transport` for first paint. The interval can remain after initial
   readiness if needed.
6. Change `JobProgressDrawer` so mount does not call `lists.jobs()`. Hydrate
   jobs only on expand/open or after the route-ready milestone, and share the
   same store/in-flight request with any jobs page or shell consumer.
7. Keep realtime job/alert event handlers, but guard count updates so they only
   mutate live counts when the base source is live or an explicit hydrated list
   has established state.
8. Confirm notification center and heavyweight drawers do not fetch full list
   payloads merely by being mounted in `PlatformShell`.

Recommended test targets:

- Add shell-summary success and degraded fixtures to the TopBar/unit test layer,
  or to the route-load e2e fixture if the shell is only tested end to end.
- Extend `e2e/22-management-evidence-load.spec.ts` from soft warnings to hard
  checks after the parent fix:
  - `nonPrimaryRequestsBeforeFirstRow <= 2`;
  - `/bff/jobs` request count before first row is `0` or `1`, never `2`;
  - shell summary success renders live counts without hitting full lists;
  - shell summary unavailable/degraded renders honest badge state and does not
    trigger immediate full-list fallback;
  - expanding `JobProgressDrawer` hydrates jobs lazily.
- Keep all route-ready tests on `domcontentloaded` plus heading/API/row or
  empty-state milestones. Do not add `networkidle`.

## Parent Absorption Checklist

Before `MGMT-LOAD-003` returns for review, confirm:

- `TopBar` has no first-mount full-list fanout for approvals, alerts, or jobs.
- `TopBar` consumes `/bff/management/shell-summary` and maps degraded/unavailable
  surfaces to visible honest state.
- `/bff/me` and `/health` are not both additional pre-first-row reads unless
  the measured non-primary budget still stays `<= 2`.
- `JobProgressDrawer` does not fetch `/bff/jobs` on mount and does not duplicate
  a jobs request before first row or empty state.
- Heavy drawer/list hydration is user-open or post-primary-content, not shell
  mount.
- Route-load tests prove the fanout budget and do not wait on network idle.
- Hosted dev evidence cites `MGMT-LOAD-001` or successor probe output after the
  frontend branch is deployed.

## Verification Notes For This Sidecar

No runtime or frontend implementation was changed by this sidecar. Verification
for this packet is source and artifact inspection only:

```bash
git status -sb
git branch --show-current
git remote -v
AI_NAME=Codex ./scripts/ai-status.sh show MGMT-LOAD-003-SIDECAR-BFF-HANDOFF
AI_NAME=Codex ./scripts/ai-status.sh show MGMT-LOAD-003
AI_NAME=Codex ./scripts/ai-status.sh show MGMT-LOAD-001
AI_NAME=Codex ./scripts/ai-status.sh show MGMT-LOAD-002
sed -n '1,260p' docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-003-fe-shell-fanout.md
sed -n '1,260p' docs/bff/execution-tasks/2026-07-01-management-console-load-gap/MGMT-LOAD-002-bff-shell-summary.md
sed -n '1,220p' docs/04/pantheon_management_console_load_gap_2026-07-01/archive/route-load-baseline-2026-07-01.md
sed -n '1,260p' /home/lupin/code/execute-plans/src/platform/components/TopBar.tsx
sed -n '1,260p' /home/lupin/code/execute-plans/src/platform/components/JobProgressDrawer.tsx
sed -n '1,260p' /home/lupin/code/execute-plans/e2e/22-management-evidence-load.spec.ts
sed -n '14920,15180p' services/control-plane/bff/main.py
```

## Reviewer Handoff

Claude should verify:

1. This packet is support-only and does not modify canonical truth, runtime
   implementation, or execute-plans source.
2. The BFF contract summary matches the merged `MGMT-LOAD-002` route and tests.
3. The frontend seam findings match the current first-mount TopBar and
   JobProgressDrawer behavior.
4. The parent absorption checklist is advisory and does not override the
   `MGMT-LOAD-003` execution packet or reviewer-owned acceptance.

This packet is ready for Claude review and parent-owner absorption decision.
