# AG-FE-DB-002 Sidecar Review Packet

| Field | Value |
|---|---|
| Task ID | `AG-FE-DB-002-SIDECAR-REVIEW` |
| Helper kind | `review_packet` |
| Parent task | `AG-FE-DB-002` - Drag/resize/add/remove/change chart editor |
| Current parent owner / reviewer | `Claude` / `Claude2` |
| Sidecar owner / reviewer | `Codex` / `Claude` |
| Date | `2026-06-22` |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |

## Purpose

This packet supports `AG-FE-DB-002` by consolidating the current review
evidence for the merged `DashboardGridEditor` implementation and by preserving
the support-only caveats that the parent owner and reviewer need before final
status closeout.

It is support-only. It does not modify L1 canonical truth, schema truth,
OpenAPI truth, BFF runtime behavior, frontend runtime behavior, widget registry
behavior, governance implementation, broker authority, or RuntimeBinding.

## Sources Used

| Source | Relevance |
|---|---|
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DB-002` | Parent owner, reviewer, active `review_approved` status, acceptance text, and review notes. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DB-002-SIDECAR-REVIEW` | Sidecar task scope, artifact path, support-only acceptance, and assigned reviewer. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-27` | Approved support caveats after parent PR `#2187`, including the Pantheon mirror versus active `execute-plans` remote split. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DB-001B` | Upstream widget runtime dependency state and delivery metadata. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-DB-001` | Backend dashboard route, validator, and deferred rule caveats. |
| GitHub PR `#2187` | Parent implementation merge facts and required check results. |
| GitHub PR `#2188` | Latest acceptance follow-up packet merge facts and required check results. |
| GitHub PR `#2189` | Parent task-brief closeout/update merge facts and required check results. |
| GitHub PR `#2190` | Acceptance follow-up 27 closeout merge facts and required check results. |
| `execute-plans/src/agora/dashboard/DashboardGridEditor.tsx` | Parent dashboard grid editor implementation under review. |
| `execute-plans/src/agora/dashboard/DashboardGridEditor.test.tsx` | Focused parent tests for layout mapping and editor gestures. |
| `support/sidecars/AG-FE-DB-002/AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-27.md` | Approved sidecar support map for post-PR `#2187` state. |

## Parent Delivery Facts

| Item | Evidence |
|---|---|
| Parent implementation PR | `https://github.com/ajoe734/pantheon/pull/2187` |
| Parent implementation PR state | `MERGED` into `dev` at `2026-06-22T02:54:37Z` |
| Parent implementation merge commit | `23b557a5ca1ff3f9847e0e5256b487d14a26bda9` |
| Parent task commit in local history | `d56b6a01` - `AG-FE-DB-002: implement DashboardGridEditor` |
| Parent follow-on PR | `https://github.com/ajoe734/pantheon/pull/2189` |
| Parent follow-on PR state | `MERGED` into `dev` at `2026-06-22T03:07:38Z` |
| Parent follow-on merge commit | `a7a27778b5c6cf590151218d7e2d924b91bfe575` |
| Parent follow-on changed files | `.orchestrator/task-briefs/ag_fe_db_002.md` only. |
| Parent active status | `review_approved`, not `done`, at the time this packet was prepared. |
| Parent review notes from `ai-status` | Claude2 approved the implementation: `DashboardGridEditor` matches SD §9.1/§9.4/§9.8 and `personalization_event.schema.json`; 16/16 tests pass; `WidgetPlacement` fields and required `PersonalizationEvent` fields/enums are covered. |
| Parent review file caveat | `ai-status` names `.orchestrator/reviews/ag_fe_db_002_review_claude2.md`, but that file is not present in this checkout or in `/home/lupin/code/pantheon/.orchestrator/reviews/`. This packet relies on the durable status review notes and PR evidence instead. |

## Sidecar And Support Chain Facts

| Item | Evidence |
|---|---|
| Follow-up 26 PRs | PR `#2182` merged at `e01f19e7a4b73e7a70d0a8b607159e7db4192d6b`; closeout PR `#2184` merged at `5452bbd18f114cfbdb74d4cd684ae1a88965c4b5`. |
| Follow-up 27 packet PR | `https://github.com/ajoe734/pantheon/pull/2188` |
| Follow-up 27 packet PR state | `MERGED` into `dev` at `2026-06-22T03:01:57Z` |
| Follow-up 27 packet merge commit | `431d75d49883fc6c9288f92927c606a3f3877dd0` |
| Follow-up 27 closeout PR | `https://github.com/ajoe734/pantheon/pull/2190` |
| Follow-up 27 closeout PR state | `MERGED` into `dev` at `2026-06-22T03:12:27Z` |
| Follow-up 27 closeout merge commit | `257e4a1909ab1fe0cec8c4241d5f01ad8a71e5eb` |
| Follow-up 27 terminal status | Archived `done` at `2026-06-22T03:12:47Z`. |
| Follow-up 27 review notes | Claude accepted the packet's Pantheon-dev versus external `execute-plans origin/dev` split, component-versus-persistence distinction, and support-only boundary. |
| This sidecar artifact | `support/sidecars/AG-FE-DB-002/AG-FE-DB-002-SIDECAR-REVIEW.md` |

## Dependency State

| Dependency | Current state | Consequence for `AG-FE-DB-002` |
|---|---|---|
| `AG-FE-DB-001B` | Archived `done`; status says widget runtime artifacts were delivered and 17/17 widget tests passed. | `DashboardGridEditor` can compose the registry and `WidgetRenderer` in the Pantheon mirror. |
| `AG-BE-DB-001` | Archived `done`; all 11 §17.5 routes, ETag/If-Match concurrency, and core A3 safety rules were reviewed as complete. Rule 4 field catalog and Rule 7 scope check remain deferred follow-ups. | Component-level DB002 review should not claim those deferred validator rules are complete. |
| `AG-FE-DB-003` / `AG-FE-DB-004` | Archived `done` in prior packets. | DB002 should remain a grid editor slice and not duplicate revision drawer, proposal preview, change log, or rollback behavior. |
| `AG-E2E-TR-001` | Active `todo`; depends on `AG-FE-DB-002`, `AG-FE-TR-002`, and `AG-XR-OPENAPI-004`. | Downstream E2E should wait for parent DB002 status closeout and delivery target reconciliation. |

## Review Matrix

| Area | Evidence | Sidecar assessment |
|---|---|---|
| `react-grid-layout` usage | `DashboardGridEditor.tsx` imports `GridLayout` and CSS from `react-grid-layout` / `react-resizable`. | Meets the grid library requirement in the Pantheon mirror implementation. |
| `WidgetPlacement` mapping | `placementsToLayout` maps `widget_id`, `x`, `y`, `w`, `h`, `min_w`, `min_h`, optional `max_w`, `max_h`, and `pinned` to grid layout fields; `layoutToPlacements` maps them back. | Required placement fields are preserved at component boundary. |
| Drag and resize | `handleLayoutChange` emits `widget_reordered`, includes before/after placements, and calls `onPlacementsChange`. Tests cover drag coordinate changes and resize dimensions. | Component-level evidence is sufficient for local editor callbacks. |
| Remove | `handleRemoveWidget` emits `widget_removed` and calls `onWidgetRemove`; tests cover event payload and callback. | Component-level remove path is covered. |
| Add registered widget | `AddWidgetPanel` lists `getActiveWidgetTypes()` and reads `getWidgetRegistryEntry()` chart kinds before invoking `onWidgetAdd`. Tests cover selecting an active widget type and chart kind. | Good UI-level registry gate. This component does not itself validate inactive external inputs because the caller owns mutation persistence. |
| Change chart | `ChangeChartPanel` derives allowed chart kinds from registry entry and emits `dashboard_recipe_changed`; tests cover callback and event payload. | Component-level chart-change path is covered. |
| `PersonalizationEvent` required fields | `emitEvent` fills `spec_version`, `event_id`, `operator_id`, optional `session_id`, `occurred_at`, and `source`. Tests cover required fields. | Matches the parent review note and local test evidence. |
| Widget rendering | Each grid cell delegates to `WidgetRenderer` and passes `allowedSensitivities`, `data`, and interaction callback context. | The component composes the registry-backed renderer instead of reimplementing widget rendering. |
| Pinned behavior | `placementsToLayout` maps `pinned` to `static`; remove button is hidden when `placement.pinned` is true. Tests cover both mapping and hidden remove button. | Pinned removal and grid static behavior are covered at component level. |
| Runtime and governance boundary | Text search of the delivered editor/test files found no `RuntimeBinding`, broker call, capital-binding route, order placement, governance execution, or management-route write. | No live trading authority or runtime binding is introduced by this slice. |
| Direct BFF persistence | `DashboardGridEditor` exposes callbacks and does not call `PATCH /bff/agora/dashboard-recipes/{recipe_id}/layout` itself. | Caller/BFF persistence remains out of this component's proof. Parent closeout should avoid claiming this component proves ETag/If-Match, `expected_version`, or `Idempotency-Key` behavior. |

## Delivery Target Caveat

The current Pantheon `dev` tree contains the DB002 editor and tests under the
in-repo `execute-plans/` mirror. The active external frontend repository
`ajoe734/execute-plans` still reports `origin/dev` at
`ee835e2e6f1037e612d7929279a11efb32c61975`, and the inspected Agora paths list
only:

```text
package-lock.json
package.json
src/lib/bff-v1/agora/types.ts
```

This means the external frontend delivery surface still lacks the DB widget
runtime, dashboard editor, dashboard compose surfaces, and dependency/layout
PATCH types documented in follow-up 27. This sidecar does not decide whether
the parent may close on Pantheon mirror evidence or must wait for active
`execute-plans` remote reconciliation. That decision remains with the parent
owner/reviewer flow.

## Verification Performed

Commands used while preparing this packet:

```bash
git status -sb
git branch --show-current
git remote -v
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DB-002
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DB-002-SIDECAR-REVIEW
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-27
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DB-001B
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-DB-001
AI_NAME=Codex ./scripts/ai-status.sh show AG-E2E-TR-001
gh pr view 2187 --repo ajoe734/pantheon --json number,state,mergedAt,mergeCommit,url,title,headRefName,baseRefName,statusCheckRollup
gh pr view 2188 --repo ajoe734/pantheon --json number,state,mergedAt,mergeCommit,url,title,headRefName,baseRefName,statusCheckRollup
gh pr view 2189 --repo ajoe734/pantheon --json number,state,mergedAt,mergeCommit,url,title,headRefName,baseRefName,statusCheckRollup
gh pr view 2190 --repo ajoe734/pantheon --json number,state,mergedAt,mergeCommit,url,title,headRefName,baseRefName,statusCheckRollup
rg -n "RuntimeBinding|broker|order|capital|governance|capability|execute|PATCH|WidgetPlacement|PersonalizationEvent|personalization|onPlacement|widget|chart|layout" execute-plans/src/agora/dashboard/DashboardGridEditor.tsx execute-plans/src/agora/dashboard/DashboardGridEditor.test.tsx
git -C /home/lupin/code/execute-plans rev-parse origin/dev
git -C /home/lupin/code/execute-plans ls-tree -r --name-only origin/dev src/agora/widgets src/agora/dashboard src/lib/bff-v1/agora package.json package-lock.json
npm --prefix execute-plans ci
npm --prefix execute-plans test -- --run src/agora/dashboard/DashboardGridEditor.test.tsx
```

Observed results:

- PR `#2187`, `#2188`, `#2189`, and `#2190` are merged into Pantheon `dev`; their
  visible Branch CI Gate and Orchestrator Sync checks reported success.
- Initial focused test attempt failed with `vitest: not found` before
  dependencies were installed in this worktree. After `npm --prefix
  execute-plans ci`, the same focused test passed: 1 file, 16 tests.
- `npm --prefix execute-plans ci` completed from the existing lockfile and
  reported 4 npm audit vulnerabilities; this support slice did not run
  `npm audit fix` or change dependency truth.
- Parent `AG-FE-DB-002` remains active `review_approved`, not `done`.
- This sidecar remains active `in_progress` until Claude review handoff.
- `FOLLOWUP-27` is archived `done`; its review notes approve the support-only
  split and caveats reused here.
- `ai-status` references a parent review file path that is not present in this
  checkout, so this packet cites durable status review notes and PR evidence.

## Reviewer Handoff

To `Claude`, sidecar reviewer:

Please review this support-only packet for:

1. Accuracy of the parent delivery facts across PR `#2187` and PR `#2189`.
2. Accuracy of the sidecar support chain facts across PR `#2188`, PR `#2190`,
   and the archived `FOLLOWUP-27` review approval.
3. Whether the review matrix correctly separates component-level proof from
   caller/BFF persistence proof.
4. Whether the delivery target caveat accurately preserves the Pantheon mirror
   versus external `execute-plans origin/dev` split without changing parent
   status or canonical truth.

If accurate, approve `AG-FE-DB-002-SIDECAR-REVIEW` and return it to `Codex` for
normal closeout. Parent `AG-FE-DB-002` remains owned by `Claude` with reviewer
`Claude2`; this sidecar does not replace the parent closeout decision.

Prepared by `Codex` for the `AG-FE-DB-002-SIDECAR-REVIEW` support slice.
