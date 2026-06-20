# AG-FE-DB-003 Sidecar Review Packet

| Field | Value |
|---|---|
| Task ID | `AG-FE-DB-003-SIDECAR-REVIEW` |
| Helper kind | `review_packet` |
| Parent task | `AG-FE-DB-003` - Widget conversation revision + before/after |
| Parent owner / reviewer | `Codex2` / `Claude` |
| Prepared by | `Codex` |
| Reviewer | `Codex2` |
| Date | `2026-06-20` |
| Mutates canonical truth | `false` |
| Status | Ready for review |

## Purpose

This packet supports `AG-FE-DB-003` by consolidating review evidence for the
merged `WidgetRevisionDrawer` implementation and by calling out the exact
handoff boundary for the parent owner.

It is support-only. It does not change L1 canonical truth, schema truth,
OpenAPI truth, BFF runtime behavior, frontend runtime behavior, widget
registry behavior, governance implementation, broker authority, or
RuntimeBinding.

## Sources Used

| Source | Relevance |
|---|---|
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DB-003` | Parent title, status, owner/reviewer, acceptance, closeout evidence, and review notes. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DB-003-SIDECAR-REVIEW` | Sidecar task scope, artifact path, support-only acceptance, and assigned reviewer. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DB-001` | Confirms upstream registry/renderers are archived `done` and available for composition. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-DB-001` | Confirms backend widget validator and dashboard recipe route family are archived `done`; records validator follow-up caveats. |
| GitHub PR `#1860` | Confirms parent implementation merged into `dev` at `b0444fa5f690ba0fc28a6d434ebe6dc53b03a0a1` with required checks green. |
| `execute-plans/src/agora/widgets/WidgetRevisionDrawer.tsx` | Parent UI implementation under review. |
| `execute-plans/src/agora/widgets/WidgetRevisionDrawer.test.tsx` | Parent focused test coverage for request, validation, before/after preview, accept, keep-both, and reject behavior. |
| `execute-plans/src/lib/bff-v1/agora/dashboard.ts` | Frontend BFF helper used for widget validation. |
| `execute-plans/src/lib/bff-v1/agora/types.ts` | Generated `WidgetSpecV2` and `validateAgoraWidget` operation truth. |
| `services/control-plane/bff/agora/dashboard/router.py` | Backend `POST /bff/agora/widgets/validate` implementation and A3 validator rules. |
| `services/control-plane/specs/agora/v2/widget_spec_v2.schema.json` | Canonical v2 WidgetSpec field boundary for this slice. |

## Parent Delivery Facts

| Item | Evidence |
|---|---|
| Parent implementation PR | `https://github.com/ajoe734/pantheon/pull/1860` |
| PR state | `MERGED` into `dev` at `2026-06-20T17:13:40Z` |
| Parent merge commit | `b0444fa5f690ba0fc28a6d434ebe6dc53b03a0a1` |
| Parent task commit | `eb2f018ad11b9e485060d7e0a75a4b13e81c36c6` |
| Files delivered by PR | `.orchestrator/task-briefs/ag_fe_db_003.md`, `WidgetRevisionDrawer.tsx`, `WidgetRevisionDrawer.test.tsx`, `execute-plans/src/lib/bff-v1/agora/dashboard.ts` |
| GitHub checks | Commit trailers, Runtime mirror guard, Smoke acceptance, and Forward to orchestrator all reported `SUCCESS` on PR `#1860`. |
| Parent active status | `review_approved`; owner `Codex2` still needs finalization. |

## Dependency State

| Dependency | Current state | Consequence for `AG-FE-DB-003` |
|---|---|---|
| `AG-FE-DB-001` | Archived `done`; WidgetRegistry, WidgetRenderer, ChartSpecRenderer, and generated type mirror are merged. | `WidgetRevisionDrawer` can compose `WidgetRenderer` for both before and after panes instead of creating a parallel renderer. |
| `AG-BE-DB-001` | Archived `done`; backend validates WidgetSpec v2 via `/bff/agora/widgets/validate`. | The drawer can validate returned specs before accept/keep-both decisions. |

## Review Matrix

| Area | Evidence | Sidecar assessment |
|---|---|---|
| WidgetSpec type boundary | `WidgetRevisionDrawer` imports `WidgetSpecV2` from generated `execute-plans/src/lib/bff-v1/agora/types.ts`. | No local duplicate WidgetSpec type or field alias was introduced. |
| Validate route boundary | `validateAgoraWidget` posts to `/bff/agora/widgets/validate`; generated operations and backend router expose the same route. | No ad-hoc validation route appears in this slice. |
| Servant revision boundary | The drawer receives `onRequestRevision` as a prop and does not define a new BFF route for assistant generation. | Parent integration can wire conversation orchestration without this component inventing routing truth. |
| Before/after preview | The drawer renders the base widget and the validated proposal through `WidgetRenderer` in separate panes. | Preview composes the existing registry-backed renderer rather than bypassing registry/chart grammar gates. |
| Diff summary | The drawer compares title, widget type, data source, chart kind, query fields, transforms, interactions, and sensitivity. | The visible diff is narrow and WidgetSpec-focused. |
| Validation failure | Invalid backend validation results show errors and disable accept / keep-both while still allowing reject. | Unsafe or unsupported generated specs cannot be accepted from this UI surface. |
| Runtime/governance boundary | Search over the delivered drawer/helper files found no `RuntimeBinding`, broker, management route, runtime route, order placement, or capital-binding call. | The slice stays outside live trading authority and runtime binding. |
| Test coverage | `WidgetRevisionDrawer.test.tsx` covers valid request/validate/preview/accept, invalid accept/keep-both blocking, and invalid reject flow. | Focused coverage matches the parent acceptance surface. |

## Verification Run

| Command | Result |
|---|---|
| `npm --prefix execute-plans ci` | Passed; installed local FE dependencies for verification. Npm reported audit findings, but this support slice does not change dependencies. |
| `npm --prefix execute-plans test -- src/agora/widgets` | Passed: 4 files, 17 tests. Includes `WidgetRevisionDrawer.test.tsx` 3/3. |
| `npm --prefix execute-plans run build:agora` | Passed: Vite built `dist/agora/agora.html` and one app bundle. |

Initial validation attempts before `npm ci` failed because `vitest` and `vite`
were not installed in this worktree. After installing dependencies from the
existing lockfile, the same focused checks passed.

## Caveats For Parent Closeout

| Caveat | Recommended handling |
|---|---|
| Parent `ai-status.json` points at `.orchestrator/reviews/ag_fe_db_003_review.md`, but that file is not present in this checkout. | Do not block on this sidecar if the status `review_notes_zh` remains the authoritative approval record; parent owner may add or correct the review artifact during finalization if needed. |
| `AG-BE-DB-001` review notes defer A3 validator Rule 4 field-catalog and Rule 7 scope-check follow-ups. | Not blocking for DB003's current drawer acceptance, but downstream owners should not claim those validator rules are complete. |
| `npm ci` reported dependency audit findings. | Out of scope for this support slice; do not run `npm audit fix` here because it would change dependency truth. |

## Reviewer Handoff

To `Codex2`, sidecar reviewer and parent owner:

- Verify this packet accurately captures parent PR `#1860`, dependency state,
  validate route truth, local verification, and support-only boundary.
- If accepted, approve this sidecar and use it as the parent closeout evidence
  summary for `AG-FE-DB-003`.
- Parent finalization remains with `Codex2`; this sidecar does not move the
  parent task to `done`.

Suggested reviewer command:

```bash
AI_NAME=Codex2 REVIEW_FILE=support/sidecars/AG-FE-DB-003/AG-FE-DB-003-SIDECAR-REVIEW.md ./scripts/ai-status.sh approve AG-FE-DB-003-SIDECAR-REVIEW "Review approved: AG-FE-DB-003 review packet captures PR #1860 merge facts, dependency state, WidgetSpec/validate route truth, focused verification, support-only boundary, and parent closeout caveats."
```

Prepared by `Codex` for the `AG-FE-DB-003-SIDECAR-REVIEW` support slice.
