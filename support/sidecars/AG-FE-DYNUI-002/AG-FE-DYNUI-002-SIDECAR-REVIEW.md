# AG-FE-DYNUI-002 Sidecar Review Packet

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-DYNUI-002-SIDECAR-REVIEW` |
| Helper parent | `AG-FE-DYNUI-002` |
| Helper kind | `review_packet` |
| Parent title | V11 Trading Room proposal preview and workspace shell |
| Parent owner / reviewer | `Codex2` / `Claude2` as of `2026-06-29` status readback |
| Parent implementation PR | execute-plans PR `#81` |
| Sidecar owner / reviewer | `Codex2` / `Codex` |
| Date | `2026-06-29` |
| Mutates canonical truth | `false` |
| Status | Ready for `Codex` support review |

This packet is support material only. It summarizes the visible parent PR
evidence, review risks, and reviewer handoff for `AG-FE-DYNUI-002`. It does
not approve the parent implementation and does not edit canonical truth,
schemas, OpenAPI, BFF runtime, frontend runtime, registry/governance, or
broker/capital authority.

## 1. Scope

`AG-FE-DYNUI-002` is now in `review` with execute-plans PR `#81` open at
commit `90d2d625010e8d3d793a5d06e36f6c5b2334e450`. This sidecar checks whether
the PR appears ready for parent review against the already merged support
packets:

- `AG-FE-DYNUI-002-SIDECAR-BFF-HANDOFF`
- `AG-FE-DYNUI-002-SIDECAR-ACCEPTANCE`
- `AG-FE-DYNUI-002-SIDECAR-ACCEPTANCE-FOLLOWUP-2`

The sidecar does not make changes in execute-plans. Findings below are for the
parent owner/reviewer to resolve in the parent PR or review process.

## 2. Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates work; sidecar packets do not override canonical truth. |
| `.orchestrator/task-briefs/ag_fe_dynui_002_sidecar_review.md` | Task scope is support-only review packet and evidence summary. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support docs should be committed narrowly. |
| `.orchestrator/skills/task-closeout-finalization.md` | `done` closeout is only after review approval, task commit, PR merge, and status closeout. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-DYNUI-002-SIDECAR-REVIEW` | Active `in_progress`, owner `Codex2`, reviewer `Codex`, artifact path is this file, `mutates_canonical: false`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-DYNUI-002` | Parent is `review`; execute-plans PR `#81` is open at commit `90d2d625010e8d3d793a5d06e36f6c5b2334e450`; local focused tests and build are reported passed; GitHub integration gate is unstable. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-DYNUI-001`, `AG-BE-DYNUI-003`, `AG-FE-DYNUI-001`, `AG-FE-TR-001` | Parent dependencies are archived `done`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-DYNUI-003`, `AG-FE-DYNUI-004`, `AG-FE-DYNUI-005`, `AG-E2E-DYNUI-001` | Downstream editor, revision drawer, visual parity, and E2E tasks remain future work. |
| `docs/04/agora_design_pack_dynui_2026-06-28/README.md` | Routes V11 proposal preview and generated workspace shell to `AG-FE-DYNUI-002`; rejects static screenshots/mock pages. |
| `docs/04/agora_design_pack_dynui_2026-06-28/source-map-and-gap-map.md` | Requires generation progress, `TradingRoomWorkspaceProposal`, all views, thumbnails/counts, data availability, warnings, personalization, and no empty dashboard/static skeleton. |
| `support/sidecars/AG-FE-DYNUI-002/AG-FE-DYNUI-002-SIDECAR-ACCEPTANCE.md` | Main acceptance checklist for this parent task. |
| `support/sidecars/AG-FE-DYNUI-002/AG-FE-DYNUI-002-SIDECAR-ACCEPTANCE-FOLLOWUP-2.md` | Current readiness gate: dependencies done; parent implementation evidence required. |
| `gh pr view 81 --repo ajoe734/execute-plans ...` | PR `#81` is open, not draft, mergeable but `UNSTABLE`; one commit, nine changed files, one failing `integration-gate` check. |
| `gh run view 28353302511 --repo ajoe734/execute-plans ...` | Run failed in job `integration-gate`; the only failed job step exposed by `gh` is `Aggregate release gate`. |
| PR `#81` release-gate comment | Overall release gate is `FAIL`; aggregate summary reports failing/warn/missing checks across Gates 0-7 and 31 failing or missing checks in the final release decision. |
| `/home/lupin/code/execute-plans` local branch `task/AG-FE-DYNUI-002` | Clean worktree at PR head `90d2d625010e8d3d793a5d06e36f6c5b2334e450`. |
| `execute-plans/src/App.tsx` | Routes `/agora/trading-room` and `/agora/trading-room/:strategyId` to `TradingRoomPage`. |
| `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` | Adds proposal create/read, accept, and workspace read helpers. |
| `execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx` | Adds V11 generation progress, proposal preview, accept path, and workspace shell; page does not call `fetch()` directly. |
| `execute-plans/src/agora/trading-room/WorkspaceProposalPreview.tsx` | Renders proposal views, thumbnails, widget counts, data availability, warnings, personalization, and registry validation. |
| `execute-plans/src/agora/widgets/registry.ts` | Adds widget/chart/interaction allowlists and blocked interactions for order/live/capital/broker/runtime-binding actions. |
| `services/control-plane/bff/agora/trading_room/router.py` | Accept route returns `data.workspaceId`, `data.workspace`, and `data.version`; it does not return a top-level `TradingRoomWorkspace` as `data`. |
| `services/control-plane/bff/agora/trading_room/test_trading_room.py` | Backend acceptance helper reads `resp.json()["data"]["workspace"]`, confirming the accept envelope shape. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## 3. Parent PR Snapshot

| Surface | Observed state |
|---|---|
| Repository / PR | `ajoe734/execute-plans` PR `#81`, `https://github.com/ajoe734/execute-plans/pull/81` |
| Base / head | `main` <- `task/AG-FE-DYNUI-002` |
| Head commit | `90d2d625010e8d3d793a5d06e36f6c5b2334e450` |
| PR state | Open, not draft, `mergeable: MERGEABLE`, `mergeStateStatus: UNSTABLE` |
| Changed files | `src/App.tsx`; Trading Room page/tests; new `WorkspaceProposalPreview.tsx`; widget registry files/tests; Trading Room BFF client/tests |
| Check rollup | `integration-gate` failed |
| Run detail | Actions run `28353302511`; job `integration-gate`; failed step `Aggregate release gate` |
| Local focused validation rerun by this sidecar | `npm test -- src/lib/bff-v1/agora/tradingRoom.test.ts src/agora/pages/trading-room/TradingRoomPage.test.tsx src/agora/widgets/registry.test.ts` passed: 3 files, 64 tests |
| Diff hygiene | `git -C /home/lupin/code/execute-plans diff --check origin/main...HEAD` passed |

## 4. Evidence Matrix

| Acceptance area | Evidence seen | Review implication |
|---|---|---|
| V11 generated types | `types.ts` includes `TradingRoomWorkspaceProposal`, `TradingRoomWorkspace`, `TradingRoomViewSpec`, `TradingRoomWidgetSpec`, and contract hashes. | Positive. Parent uses the v1.5 generated surface rather than inventing local durable types for the main proposal/workspace objects. |
| BFF client boundary | Proposal create/read/accept/workspace read helpers are in `src/lib/bff-v1/agora/tradingRoom.ts`; page-level grep found no `fetch(` in `TradingRoomPage.tsx`. | Positive. Page uses typed helper boundary. |
| Join starts proposal generation | `StrategyWorkspaceView` calls `createTradingRoomWorkspaceProposal(strategyId, { strategyVersion, tradingRoomReady, personalizationHints }, { idempotencyKey })`. | Positive for the main entry path. |
| Generation state | `TradingRoomGenerationProgress` renders while proposal generation is pending and lists generation steps including Views/widgets/layout/personalization. | Positive. Tests cover pending generation state. |
| Proposal preview | `WorkspaceProposalPreview` renders proposal rationale, `views[]`, per-view thumbnail, widget count, layout template, data availability, warnings, personalization, and registry validation. | Positive. Tests cover all mocked V11 views, thumbnails, counts, data availability, warnings, personalization. |
| Winner Branch seven views | Test fixture includes seven V11 views and asserts each preview card/thumbnail/count is present. | Positive at unit level. Reviewer should still require browser or screenshot evidence if parent closeout claims visual readiness. |
| No `DashboardRecipeV2` substitution | Scoped grep under Trading Room page/client found no `DashboardRecipeV2`/`getDashboardRecipeById`; tests assert old `strategy-recipe-*` placeholders are absent for selected strategies. | Positive. Legacy `dashboard_recipe_id` remains only as part of aggregate strategy entry type/test data, not as V11 source. |
| Workspace shell | `TradingRoomWorkspaceShell` renders tabs and widget cards from `workspace.views`, active view, and `ChartSpecRenderer`. | Intended positive, but blocked by Finding 1 because accept currently unwraps the real backend envelope incorrectly. |
| Widget safety | `WorkspaceProposalPreview` validates widget type, data source, chart kind, transform, interaction, and sensitivity against `registry.ts`; `registry.ts` blocks `place_order`, `submit_order`, `enable_live`, `bind_capital`, `runtime_binding`, and `invoke_broker`. | Positive. Reviewer should ensure blocked actions stay impossible after downstream editor tasks. |
| Downstream scope separation | No layout PATCH, widget mutation, revision drawer, version history, rollback, or E2E flow was added in this parent PR. | Positive. Preview footer has non-mutating "adjust/regenerate/back" controls; no downstream persistence behavior appears implemented. |
| Typed error states | Client helpers throw plain `Error(message)` on most non-OK responses; UI stores `proposalError` as a string. Tests do not cover `403`, `409`, `412`, `422`, or `501` proposal/workspace handling. | Gap. See Finding 2. |
| Remote checks | PR check rollup is failing; aggregate release gate summary reports broad failing/warn/missing checks. | Delivery blocker. See Finding 3. |

## 5. Findings For Parent Review

### Finding 1 - Blocker: accept response envelope is unwrapped incorrectly

The current frontend helper expects the accept response `data` to be a
`TradingRoomWorkspace`:

- `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` returns
  `extractDetail<TradingRoomWorkspace>(responseBody)` from
  `acceptTradingRoomWorkspaceProposal`.
- `extractDetail` returns `root.data`.
- `TradingRoomPage` passes that value directly to `TradingRoomWorkspaceShell`,
  which reads `workspace.views`.

The current BFF accept route returns:

```json
{
  "data": {
    "workspaceId": "...",
    "workspace": { "...": "TradingRoomWorkspace" },
    "version": { "...": "version metadata" }
  },
  "meta": { "...": "..." }
}
```

Backend tests confirm this shape via `resp.json()["data"]["workspace"]`.
Therefore PR `#81` can pass local unit tests while failing against the real BFF:
`workspace.views` would be read from the accept wrapper object, not from
`data.workspace`.

Recommended parent fix:

1. Add an accept response adapter that accepts `{ workspaceId, workspace,
   version }` and returns `workspace`, or falls back to
   `getTradingRoomWorkspace(workspaceId)` when only the ID is present.
2. Update `tradingRoom.test.ts` to mock the real accept envelope.
3. Update `TradingRoomPage.test.tsx` so the accept-to-shell test fails if the
   helper returns the wrapper instead of a `TradingRoomWorkspace`.

### Finding 2 - Gap: proposal/workspace error handling is not status-typed yet

The support acceptance packet calls for typed handling for `403`, `404`, `409`,
`412`, `422`, and `501`/capability-not-ready. Current PR evidence shows:

- `createTradingRoomWorkspaceProposal` and `acceptTradingRoomWorkspaceProposal`
  throw plain `Error(message)` for non-OK responses.
- `getTradingRoomWorkspaceProposal` and `getTradingRoomWorkspace` return `null`
  on `404`, but the page does not exercise stale-navigation clearing through a
  selected proposal/workspace route.
- `StrategyWorkspaceView` stores `proposalError` as a string and does not
  distinguish scope failures, stale workflow state, validation failures,
  precondition failures, or capability-not-ready terminal state.
- Focused tests do not cover these statuses for proposal/workspace generation,
  read, accept, or workspace load.

Recommended parent fix:

1. Preserve HTTP status and error code in a typed BFF error object.
2. Add UI state rules for `403`, `404`, `409`, `412`, `422`, and `501`.
3. Add focused tests proving `403` clears proposal/workspace state, `501` does
   not render fixtures/recipe fallback, `409` shows stale proposal workflow,
   and validation/precondition errors are visible without fake data.

### Finding 3 - Delivery blocker: execute-plans PR #81 is not mergeable yet

PR `#81` is open and `mergeable`, but its merge state is `UNSTABLE` because the
`integration-gate` check failed. `gh run view 28353302511` reports the failed
job step as `Aggregate release gate`. The release-gate PR comment reports
overall `FAIL`, including failing/warn/missing checks across npm install,
contract drift aggregation, hosted browser probe, Playwright user-flow evidence,
a11y/perf evidence, and final release decision.

This sidecar does not determine whether those aggregate failures are in scope
for `AG-FE-DYNUI-002`. It does mean the parent task cannot be considered
merge-ready until the check is repaired, rerun green, or the repository policy
explicitly marks the aggregate gate non-blocking for this PR.

## 6. Reviewer Handoff

Reviewer: `Codex`

Please review this packet on support-packet terms only:

1. It stays support-only and does not modify canonical truth or runtime.
2. It correctly summarizes the visible parent PR evidence.
3. It identifies the accept-envelope mismatch as a parent blocker.
4. It separates local focused test pass from incomplete status-typed error
   coverage.
5. It records that PR `#81` remains open/unstable and is not a completed parent
   delivery.

Suggested reviewer approval command:

```bash
AI_NAME=Codex REVIEW_FILE=support/sidecars/AG-FE-DYNUI-002/AG-FE-DYNUI-002-SIDECAR-REVIEW.md \
  REVIEW_NOTES_ZH="審核通過：AG-FE-DYNUI-002 sidecar review packet 保持 support-only，整理 execute-plans PR #81 的 parent evidence，指出核心 preview/generation/shell tests pass，但 accept response envelope 與 real BFF data.workspace 形狀不符是 parent blocker，proposal/workspace status-typed error coverage 仍不足，且 PR #81 integration-gate aggregate release gate 仍 UNSTABLE；此 packet 不批准 parent implementation，也不修改 canonical/runtime。" \
  ./scripts/ai-status.sh approve AG-FE-DYNUI-002-SIDECAR-REVIEW \
  "Review packet approved; parent AG-FE-DYNUI-002 still needs parent PR fixes/check resolution before merge-ready."
```

Suggested reopen command:

```bash
AI_NAME=Codex ./scripts/ai-status.sh reopen AG-FE-DYNUI-002-SIDECAR-REVIEW \
  "Describe the missing evidence, incorrect finding, or support-scope issue to correct before approval."
```

## 7. Validation Run

Commands run from the Pantheon sidecar worktree unless noted:

```bash
git status -sb
git branch --show-current
git remote -v
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-DYNUI-002-SIDECAR-REVIEW
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-DYNUI-002
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-DYNUI-001
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-DYNUI-003
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-DYNUI-001
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-TR-001
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-DYNUI-003
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-DYNUI-004
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-DYNUI-005
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-E2E-DYNUI-001
gh pr view 81 --repo ajoe734/execute-plans --json number,state,isDraft,mergeable,mergeStateStatus,reviewDecision,headRefName,headRefOid,baseRefName,mergeCommit,url,title,statusCheckRollup,comments,reviews,files,commits,changedFiles,additions,deletions
gh run view 28353302511 --repo ajoe734/execute-plans --json databaseId,displayTitle,event,status,conclusion,createdAt,updatedAt,url,headSha,jobs
git -C /home/lupin/code/execute-plans status -sb
git -C /home/lupin/code/execute-plans rev-parse HEAD
git -C /home/lupin/code/execute-plans diff --check origin/main...HEAD
npm test -- src/lib/bff-v1/agora/tradingRoom.test.ts src/agora/pages/trading-room/TradingRoomPage.test.tsx src/agora/widgets/registry.test.ts
git diff --check -- .orchestrator/task-briefs/ag_fe_dynui_002_sidecar_review.md support/sidecars/AG-FE-DYNUI-002/AG-FE-DYNUI-002-SIDECAR-REVIEW.md
```

Observed results:

- Sidecar branch is `task/AG-FE-DYNUI-002-SIDECAR-REVIEW`.
- Parent `AG-FE-DYNUI-002` is in `review`; parent PR `#81` is open/unstable at
  `90d2d625010e8d3d793a5d06e36f6c5b2334e450`.
- Dependencies `AG-XR-DYNUI-001`, `AG-BE-DYNUI-003`, `AG-FE-DYNUI-001`, and
  `AG-FE-TR-001` are archived `done`.
- Downstream `AG-FE-DYNUI-003`, `AG-FE-DYNUI-004`, `AG-FE-DYNUI-005`, and
  `AG-E2E-DYNUI-001` remain future work.
- execute-plans focused tests passed: 3 test files, 64 tests.
- Pantheon and execute-plans diff checks passed.
- No parent runtime files were modified by this sidecar.

Prepared by `Codex2` for the `AG-FE-DYNUI-002-SIDECAR-REVIEW` support slice.
