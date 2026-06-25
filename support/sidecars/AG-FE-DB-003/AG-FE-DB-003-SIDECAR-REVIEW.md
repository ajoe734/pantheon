# AG-FE-DB-003 Sidecar Review Packet

| Field | Value |
|---|---|
| Task ID | `AG-FE-DB-003-SIDECAR-REVIEW` |
| Helper kind | `review_packet` |
| Parent task | `AG-FE-DB-003` - Widget conversation revision + before/after |
| Parent owner / reviewer | `Codex2` / `Claude` |
| Prepared by | `Codex` |
| Reviewer | `Claude` |
| Date | `2026-06-20` |
| Mutates canonical truth | `false` |
| Status | Review approved; owner finalizing |

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
| GitHub PR `#1863` | Confirms parent closeout merged into `dev` at `ac0d55c1bffdd5791c529cc915ca531e08c2c8d2`. |
| GitHub PR `#1864` | Confirms this sidecar packet merged into `dev` at `b15a9f84d80a9f845318cd4a5aa26af5d120fa22`. |
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
| Parent closeout PR | `https://github.com/ajoe734/pantheon/pull/1863` |
| Parent closeout merge commit | `ac0d55c1bffdd5791c529cc915ca531e08c2c8d2` |
| Files delivered by PR | `.orchestrator/task-briefs/ag_fe_db_003.md`, `WidgetRevisionDrawer.tsx`, `WidgetRevisionDrawer.test.tsx`, `execute-plans/src/lib/bff-v1/agora/dashboard.ts` |
| GitHub checks | Commit trailers, Runtime mirror guard, Smoke acceptance, and Forward to orchestrator all reported `SUCCESS` on PR `#1860`. |
| Parent terminal status | Archived `done` by owner `Codex2` at `2026-06-20T17:27:18Z`. |

## Sidecar Delivery Facts

| Item | Evidence |
|---|---|
| Sidecar packet PR | `https://github.com/ajoe734/pantheon/pull/1864` |
| PR state | `MERGED` into `dev` at `2026-06-20T17:30:05Z` |
| Sidecar merge commit | `b15a9f84d80a9f845318cd4a5aa26af5d120fa22` |
| Sidecar packet commit | `0f93791ffadaea614edc9baa3e762ffcd0f4ab6d` |
| Review approval | `Claude` approved the packet in `ai-status` with the support-only boundary, verification results, dependency state, and parent closeout caveats accepted. |

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

## Residual Caveats

| Caveat | Recommended handling |
|---|---|
| Parent archive still points at `.orchestrator/reviews/ag_fe_db_003_review.md`, but that file is not present in this checkout. | Do not reopen this sidecar for that archival path mismatch; parent closeout is already archived `done` with review notes and PR #1863 evidence. |
| `AG-BE-DB-001` review notes defer A3 validator Rule 4 field-catalog and Rule 7 scope-check follow-ups. | Not blocking for DB003's current drawer acceptance, but downstream owners should not claim those validator rules are complete. |
| `npm ci` reported dependency audit findings. | Out of scope for this support slice; do not run `npm audit fix` here because it would change dependency truth. |

## Review Approval And Owner Closeout

Claude approved this sidecar packet in `ai-status` after checking that it
accurately captures PR #1860 merge facts, dependency state, WidgetSpec/validate
route truth, focused verification, support-only boundary, and parent closeout
caveats. The approval also confirms no L1 canonical truth, schema, OpenAPI, BFF
runtime, RuntimeBinding, or broker behavior was changed by this sidecar.

Owner closeout scope for `Codex` is limited to making this approved support
packet durable, recording the updated parent/sidecar merge facts above, and
then moving `AG-FE-DB-003-SIDECAR-REVIEW` to `done`.

Closeout verification for this finalization update:

- `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DB-003-SIDECAR-REVIEW`
- `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DB-003`
- `gh pr list --state all --head task/AG-FE-DB-003-SIDECAR-REVIEW --json number,state,mergedAt,mergeCommit,url,title --limit 5`
- `git diff --check -- support/sidecars/AG-FE-DB-003/AG-FE-DB-003-SIDECAR-REVIEW.md`

Prepared by `Codex` for the `AG-FE-DB-003-SIDECAR-REVIEW` support slice.
