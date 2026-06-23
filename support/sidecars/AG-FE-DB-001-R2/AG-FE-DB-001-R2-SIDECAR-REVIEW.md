# AG-FE-DB-001-R2 Sidecar Review Packet

| Field | Value |
|---|---|
| Task ID | `AG-FE-DB-001-R2-SIDECAR-REVIEW` |
| Helper kind | `review_packet` |
| Parent task | `AG-FE-DB-001-R2` - Agora dashboard widget runtime in execute-plans |
| Current parent owner / reviewer | `Claude` / `Claude2` |
| Sidecar owner / reviewer | `Codex` / `Claude` |
| Date | `2026-06-23` |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |

## Purpose

This packet supports `AG-FE-DB-001-R2` by consolidating the latest parent review
evidence, PR evidence, and delivery-target caveats for the redo widget-runtime
slice. It is support-only. It does not modify L1 canonical truth, contract
truth, OpenAPI truth, BFF runtime behavior, frontend runtime behavior,
governance implementation, broker authority, or RuntimeBinding.

## Sources Used

| Source | Relevance |
|---|---|
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DB-001-R2` | Parent owner, reviewer, `review_approved` status, acceptance text, and Claude2 review notes. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DB-001-R2-SIDECAR-REVIEW` | Sidecar task scope, artifact path, support-only acceptance, and assigned reviewer. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DB-001B` | Archived baseline delivery for the widget runtime artifacts and 17/17 widget tests. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DB-002` | Downstream dashboard editor closeout state and dependency context. |
| `gh pr view 2280 --repo ajoe734/pantheon --json ...` | Parent PR merge facts, file list, commits, checks, and auto-merge metadata. |
| `gh pr checks 2280 --repo ajoe734/pantheon` | Current visible GitHub check outcomes for PR `#2280`. |
| `gh pr view 2282 --repo ajoe734/pantheon --json ...` | Parent closeout PR merge facts and task-brief `done` sync evidence. |
| `gh pr checks 2282 --repo ajoe734/pantheon` | Current visible GitHub check outcomes for PR `#2282`. |
| `/tmp/ag-fe-db-001-r2-review.md` | Claude2 review record captured by `ai-status` as the parent `review_file`. |
| `git show 32cc9b90083d6640fab725406b699d92d2d89ae5:...` | Merged Pantheon `origin/dev` file content for widget runtime evidence. |
| `/home/lupin/code/execute-plans` `origin/dev` inspection | External frontend repository delivery-target check required by the parent brief's anti-phantom rule. |

## Parent Lifecycle And PR Facts

| Item | Evidence |
|---|---|
| Parent active status from `ai-status` | `review_approved` at packet time; `ai-status` had not yet reflected the later task-brief closeout PR. |
| Parent task brief status on Pantheon `origin/dev` | `done` after PR `#2282`, with next note: PR `#2280` merged, deliverables verified in `origin/dev`, registry checksum consistent, BFF-only data path, security gates active, CI 3/3. |
| Parent review notes | Claude2 approved the registry, `WidgetRenderer`, `ChartSpecRenderer`, BFF-only data path, safe declarative rendering, CI gates, and scatter size-encoding improvement. |
| Parent review file | `/tmp/ag-fe-db-001-r2-review.md`; this sidecar records it as external review evidence and does not move that temporary file into repo truth. |
| Parent PR | `https://github.com/ajoe734/pantheon/pull/2280` |
| PR state | `MERGED` into `dev` at `2026-06-23T01:10:28Z`. |
| Merge commit | `32cc9b90083d6640fab725406b699d92d2d89ae5` |
| PR base / head | `dev` / `task/AG-FE-DB-001-R2` |
| PR changed files | `.orchestrator/task-briefs/ag_fe_db_001_r2.md`; `execute-plans/src/agora/widgets/ChartSpecRenderer.tsx`. |
| PR diff size | 2 files, 28 insertions, 3 deletions. |
| Visible checks | Branch CI Gate `Commit trailers`, `Runtime mirror guard`, and `Smoke acceptance` passed on both visible runs; `Forward to orchestrator` passed. |
| Parent closeout PR | PR `#2282` merged into `dev` at `2026-06-23T01:23:45Z`; merge commit `4b4e297004dd80570ee0c193db1e1c831c3e1ab2`. |
| Parent closeout checks | PR `#2282` visible Branch CI Gate and Orchestrator Sync checks passed. |
| Lifecycle freshness caveat | `ai-status show` still reports `review_approved`, while the task brief on Pantheon `origin/dev` reports `done`. This packet records both surfaces and does not resolve the status mirror drift. |

## R2 Delta Assessment

`AG-FE-DB-001-R2` is a redo/supporting correction over the prior widget runtime
baseline. The actual PR `#2280` implementation delta is intentionally narrow:

| Area | Evidence | Sidecar assessment |
|---|---|---|
| Scatter size encoding | Commit `dddcc31bad3960813c01c91ead625be13c2d3b01` adds `sizeField`, `maxSizeValue`, `symbolSize`, and a third scatter data coordinate when A3 `size` encoding is present. | Correctly closes the scatter/bubble gap called out by the parent review. |
| Chart renderer scope | The R2 commit message says it owns only `execute-plans/src/agora/widgets/ChartSpecRenderer.tsx` and does not change `registry.ts`, `WidgetRenderer.tsx`, or `package.json`. | Scope matches the merged PR file list. |
| Verification claim | Commit trailer records `Verified: npx vitest run src/agora/widgets/ - 17/17 tests passed`. | This sidecar did not rerun frontend tests; it records the owner commit claim plus Claude2 review approval. |
| Task brief sync | Commit `1fff538a288bb7bea40c7f7909077bf6cf2caed2` updates the task brief toward PR merge. | The task brief is not fully fresh after merge; use `ai-status`/GitHub as current lifecycle truth. |

## Runtime Baseline Evidence

The broader widget-runtime acceptance mostly comes from the previously delivered
baseline plus Claude2's parent review, not from new R2 code beyond scatter size
encoding.

| Area | Evidence | Sidecar assessment |
|---|---|---|
| Registry source | `registry.ts` imports `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/widget_registry.v1.json`. | Registry is derived from A3 catalog rather than hand-coded widget definitions. |
| Contract hashes | `registry.ts` exports `AGORA_WIDGET_CONTRACT_HASHES`; registry tests assert hash values for the registry and schemas. | Supports frontend/backend checksum consistency claims. |
| Active widget enforcement | `getActiveWidgetTypes()` filters `status === "active"`; `validateWidgetSpecAgainstRegistry` rejects inactive or unregistered widget types. | Meets the "only active A3 widgets render" gate at registry validation. |
| Unsafe interactions | `BLOCKED_INTERACTION_KINDS` includes `place_order`, `submit_order`, `enable_live`, `bind_capital`, `runtime_binding`, and `invoke_broker`; tests reject `place_order`. | Preserves no-order/no-capital/no-RuntimeBinding authority boundary. |
| Widget rendering gate | `WidgetRenderer.tsx` calls `validateWidgetSpecAgainstRegistry`, checks `canReadSensitivity`, and delegates chart widgets to `ChartSpecRenderer`. | Keeps validation and sensitivity checks before render. |
| BFF-only data path | `WidgetRenderer` receives `data?: ChartDataRow[]`; its test says it renders chart widgets without fetching from `data_source_id`. | Component does not directly fetch. Caller/BFF data loading remains outside this slice. |
| Chart renderer dispatch | `ChartSpecRenderer` dispatches simple chart kinds through Recharts, complex chart kinds through ECharts, and table/timeline/stacked_bar through built-in renderers. | Matches Claude2 review notes for A3 grammar coverage. |
| Renderer safety | `UNSAFE_KEY_PATTERN` and `UNSAFE_STRING_PATTERN` reject callback, HTML/script, eval, function, iframe, and event-handler markers in chart specs. | Preserves declarative-only agent output boundary. |
| Dependencies | Pantheon `origin/dev` `execute-plans/package.json` includes `echarts`, `echarts-for-react`, `react-grid-layout`, `recharts`, and `@types/react-grid-layout`. | Supports the chart/grid library requirement inside the Pantheon mirror tree. |

## Delivery Target Caveat

The parent brief contains a hard anti-phantom delivery rule: the frontend
artifacts must exist in the real `execute-plans` repository on `origin/dev`
after a merged PR, not only in a Pantheon in-repo mirror.

Current evidence is split:

| Target | Observed state |
|---|---|
| Pantheon repo `origin/dev` | Updated to merge commit `32cc9b90083d6640fab725406b699d92d2d89ae5`; `execute-plans/src/agora/widgets/ChartSpecRenderer.tsx` contains the R2 scatter size-encoding code. |
| Pantheon task brief closeout | PR `#2282` updates `.orchestrator/task-briefs/ag_fe_db_001_r2.md` to `done` and states deliverables were verified in `origin/dev`. |
| External repo `ajoe734/execute-plans` `origin/dev` | Updated to `98e7189beb63d1a02f9c98db56416f92e41ced22`; inspected tree has no `src/agora/widgets/ChartSpecRenderer.tsx` and no `src/agora/widgets/` directory. |
| External repo PR lookup | `gh pr view 2280 --repo ajoe734/execute-plans` could not resolve a PR with that number. |
| External repo dependencies | `package.json` has `recharts`, but not `echarts`, `echarts-for-react`, `react-grid-layout`, or `@types/react-grid-layout` on `origin/dev`. |

This sidecar does not decide whether parent `AG-FE-DB-001-R2` may close on
Pantheon `dev` evidence or must reconcile the external `execute-plans` delivery
target first. It preserves the discrepancy for the parent owner/reviewer flow
because the parent task text explicitly treats missing external
`execute-plans origin/dev` artifacts as a non-completion condition.

## Downstream Context

| Dependency / downstream | State | Consequence |
|---|---|---|
| `AG-FE-DB-001B` | Archived `done`; recorded widget artifacts in `execute-plans`, 17/17 widget tests, and delivery commit `6062cb2c`. | Provides the baseline runtime that R2 corrects, but the external repo caveat above remains separately relevant. |
| `AG-FE-DB-002` | Archived `done`; `DashboardGridEditor` passed 16/16 tests and composes widget runtime behavior. | DB002 can use the Pantheon mirror runtime; external frontend delivery should not be inferred from DB002 alone. |

## Verification Performed

Commands used while preparing this packet:

```bash
git status -sb
git branch --show-current
git remote -v
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DB-001-R2
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DB-001-R2-SIDECAR-REVIEW
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DB-001B
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DB-002
gh pr view 2280 --repo ajoe734/pantheon --json number,state,isDraft,mergedAt,mergeCommit,url,title,headRefName,baseRefName,commits,files,statusCheckRollup,reviewDecision,autoMergeRequest
gh pr checks 2280 --repo ajoe734/pantheon
gh pr diff 2280 --repo ajoe734/pantheon --name-only
gh pr view 2282 --repo ajoe734/pantheon --json number,state,isDraft,mergedAt,mergeCommit,url,title,headRefName,baseRefName,commits,files,statusCheckRollup
gh pr checks 2282 --repo ajoe734/pantheon
sed -n '1,220p' /tmp/ag-fe-db-001-r2-review.md
git fetch origin dev:refs/remotes/origin/dev
git show --stat --oneline 32cc9b90083d6640fab725406b699d92d2d89ae5
git show --stat --oneline 4b4e2970
git show dddcc31bad3960813c01c91ead625be13c2d3b01 -- execute-plans/src/agora/widgets/ChartSpecRenderer.tsx
git show origin/dev:.orchestrator/task-briefs/ag_fe_db_001_r2.md
git show origin/dev:execute-plans/src/agora/widgets/ChartSpecRenderer.tsx | rg -n "scatter|size|symbolSize|maxSizeValue"
git show origin/dev:execute-plans/package.json | rg -n "echarts|echarts-for-react|react-grid-layout|@types/react-grid-layout|recharts"
git -C /home/lupin/code/execute-plans fetch origin dev:refs/remotes/origin/dev
git -C /home/lupin/code/execute-plans rev-parse origin/dev
git -C /home/lupin/code/execute-plans ls-tree -r --name-only origin/dev src/agora/widgets src/agora/dashboard src/lib/bff-v1/agora package.json package-lock.json
git -C /home/lupin/code/execute-plans show origin/dev:src/agora/widgets/ChartSpecRenderer.tsx
git -C /home/lupin/code/execute-plans show origin/dev:package.json | rg -n "echarts|echarts-for-react|react-grid-layout|@types/react-grid-layout|recharts"
gh pr view 2280 --repo ajoe734/execute-plans --json number,state,url,title,headRefName,baseRefName,mergedAt,mergeCommit
git -C /home/lupin/code/execute-plans ls-remote --heads origin task/AG-FE-DB-001-R2 dev main
```

Observed results:

- Pantheon PR `#2280` is merged into `dev` at `32cc9b90083d6640fab725406b699d92d2d89ae5`.
- Parent closeout PR `#2282` is merged into `dev` at `4b4e297004dd80570ee0c193db1e1c831c3e1ab2`.
- Visible GitHub checks for PR `#2280` passed.
- Visible GitHub checks for PR `#2282` passed.
- Parent `ai-status` still reports `review_approved`, while Pantheon `origin/dev` task brief reports `done`.
- Pantheon `origin/dev` contains the R2 scatter size-encoding change.
- External `execute-plans origin/dev` does not contain `src/agora/widgets/ChartSpecRenderer.tsx` or the widget runtime directory.
- This sidecar changed only this support artifact.

## Reviewer Handoff

To `Claude`, sidecar reviewer:

Please review this support-only packet for:

1. Accuracy of the parent lifecycle and PR `#2280` merge/check evidence.
2. Accuracy of the R2 delta boundary: scatter size encoding changed; registry,
   `WidgetRenderer`, package dependencies, and most runtime proof come from the
   already-delivered baseline plus Claude2 review.
3. Whether the delivery target caveat correctly preserves the parent brief's
   anti-phantom rule without changing parent task status or canonical truth.
4. Whether the packet correctly records the lifecycle-surface mismatch:
   `ai-status` still says `review_approved`, while the task brief on Pantheon
   `origin/dev` says `done` after PR `#2282`.
5. Whether the packet is acceptable as a reviewer-facing evidence summary for
   parent owner/reviewer closeout decisions.

If accurate, approve `AG-FE-DB-001-R2-SIDECAR-REVIEW` and return it to `Codex`
for normal closeout. Parent `AG-FE-DB-001-R2` remains owned by `Claude` with
reviewer `Claude2`; this sidecar does not replace the parent closeout decision.

Prepared by `Codex` for the `AG-FE-DB-001-R2-SIDECAR-REVIEW` support slice.
