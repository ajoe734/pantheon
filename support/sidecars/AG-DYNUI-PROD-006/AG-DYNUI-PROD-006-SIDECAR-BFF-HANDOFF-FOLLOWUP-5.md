# AG-DYNUI-PROD-006 BFF and Frontend Handoff Packet - Follow-up 5

| Field | Value |
|---|---|
| Parent task | `AG-DYNUI-PROD-006` |
| Parent title | Hosted Winner Branch E2E publish gate |
| Parent owner / reviewer | `Codex` / `Claude2` |
| Sidecar task | `AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` |
| Sidecar owner / reviewer | `Codex` / `Codex2` |
| Prior sidecars | `AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF` (`done`, PR #2869), `-FOLLOWUP-2` (`done`, PR #2879), `-FOLLOWUP-3` (`done`, PR #2882), `-FOLLOWUP-4` (`done`, PR #2884) |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-04` |
| Mutates canonical | `false` |

This is a support artifact only. It does not change canonical truth, L1
contracts, BFF runtime code, `execute-plans` frontend code, registry behavior,
or governance behavior. The parent owner (`Codex`) and reviewer (`Claude2`)
decide whether and how to absorb this packet into the mainline closeout.

---

## 1. Why This Follow-up Exists

`FOLLOWUP-4` recommended pausing further mechanical `bff_handoff_packet`
re-checks until a concrete trigger fired: PR #171 merges, a new hosted deploy
occurs, `AG-DYNUI-PROD-003` or `AG-DYNUI-PROD-005` produces a new PR/branch, or
the parent `AG-DYNUI-PROD-006` task record changes.

This `FOLLOWUP-5` was nevertheless dispatched by the supervisor. Unlike a pure
repeat of `FOLLOWUP-4`, this pass did find one valid trigger event:
`AG-DYNUI-PROD-003` now has standalone `execute-plans` PR #173 open and green.
That changes the readiness picture from "PROD-003 still needs a scoped
re-implementation" to "the scoped re-implementation exists, is clean, and is
waiting for review/merge/deploy evidence." All other blockers remain.

---

## 2. Sources Read And Current Findings

| Source | Finding |
|---|---|
| `.orchestrator/task-briefs/ag_dynui_prod_006_sidecar_bff_handoff_followup_5.md` | Scope is support-only: prepare BFF/frontend handoff materials for `AG-DYNUI-PROD-006`; do not modify canonical truth. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` | Sidecar is `in_progress`, owner `Codex`, reviewer `Codex2`, depends on `AG-DYNUI-PROD-001` and `AG-DYNUI-PROD-004`, artifact path is this packet. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-DYNUI-PROD-006` | Parent is still `todo`, owner `Codex`, reviewer `Claude2`, `last_update: 2026-07-04T00:09:32Z`; no parent task PR exists in either repo. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-DYNUI-PROD-002` + `gh pr view 171 --repo ajoe734/execute-plans` | Still `review_approved`; PR #171 remains `OPEN`, `MERGEABLE`, `CLEAN`, `reviews: []`, with `integration-gate` success. No auto-merge request. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-DYNUI-PROD-003` + `gh pr view 173 --repo ajoe734/execute-plans` | Still `review_approved`, but now has standalone PR #173 open: `MERGEABLE`, `CLEAN`, `reviews: []`, `integration-gate` success, commit `2b054ab9f0ad025c204bbf13251848caf8fe4599`, 3 changed files, +286/-84. |
| `gh pr diff 173 --repo ajoe734/execute-plans --name-only` | PR #173 changes `src/agora/pages/trading-room/TradingRoomPage.tsx`, `TradingRoomPage.test.tsx`, and `src/routes/agora.tsx`. |
| `gh pr view 2888` / `gh pr view 2891` on `ajoe734/pantheon` | Pantheon evidence/closeout docs for `AG-DYNUI-PROD-003` have advanced after `FOLLOWUP-4`: PR #2888 merged at `2026-07-04T03:36:06Z` (`e323bcbb...`), PR #2891 merged at `2026-07-04T04:28:29Z` (`0e7cf4da...`). |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-DYNUI-PROD-005` | Still `todo`, owner `Claude`, reviewer `Codex2`, unchanged `last_update: 2026-07-04T00:09:32Z`; no direct PR exists in pantheon or standalone `execute-plans`. |
| Hosted probes | FE deployment is still `execute-plans` commit `dd597405e014cc91cf73f4ea2e96a561fcbf9c61`, deployed `20260704T012041Z`; BFF `/health` is ok at version `0.2.0`. PR #171 and PR #173 are not deployed. |
| Approval queue check | This worktree still lacks `.orchestrator/approval-queue.json`; central `/home/lupin/code/pantheon/.orchestrator/approval-queue.json` exists and has no exact `AG-DYNUI-PROD`, PR #171, or PR #173 match. |
| BFF/frontend inventory re-check | Backend proposal/workspace/widget-revision/version/rollback routes are still present in `router.py`; local in-tree `tradingRoom.ts` still exports only the older aggregate/decision-event client functions; `execute-plans/e2e/` still has only `13-agora.spec.ts` for Agora coverage. |

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned, per the task-scoped read-order instruction.

---

## 3. Delta Since Follow-up 4

| Trigger named by `FOLLOWUP-4` | Current result | Effect on parent readiness |
|---|---|---|
| PR #171 merges | **No.** PR #171 is still open, clean, mergeable, unreviewed. | `PROD-002` remains blocked on human merge approval, then deploy evidence. |
| New hosted dev FE deploy | **No.** `deployment.json` still reports `dd597405e014cc91cf73f4ea2e96a561fcbf9c61` from `20260704T012041Z`. | Neither `PROD-002` nor `PROD-003` is visible on hosted dev FE yet. |
| `PROD-003` new PR/branch | **Yes.** Standalone `execute-plans` PR #173 now exists, is clean/mergeable, and has a passing integration gate. | This is real progress: the standalone re-implementation has landed as a PR. It is not merged, reviewed, deployed, or screenshot-proven yet. |
| `PROD-005` new PR/branch | **No.** No direct pantheon or execute-plans PR for `task/AG-DYNUI-PROD-005`; central status is still `todo`. | Full proposal/grid/widget revision/version/rollback E2E remains blocked. |
| Parent `PROD-006` status/branch changes | **No.** Parent is still `todo`, no direct PR in pantheon or execute-plans. | The hosted E2E parent still should not be closed or treated as started. |

---

## 4. Updated Readiness View

| Dependency | Current state | What changed since `FOLLOWUP-4` | Remaining blocker |
|---|---|---|---|
| `AG-DYNUI-PROD-002` | `review_approved`; execute-plans PR #171 open, clean, mergeable, green, zero reviews. | No material change. Supervisor emitted another finalize-dispatch status update, but the PR and hosted deployment did not move. | Human merge approval, then dev deploy, then hosted screenshot/evidence before done. |
| `AG-DYNUI-PROD-003` | `review_approved`; execute-plans PR #173 open, clean, mergeable, green, zero reviews; pantheon docs/evidence PRs #2888 and #2891 merged. | **Material progress.** The standalone re-implementation follow-up that prior packets said was needed now exists as PR #173. | Review/merge PR #173, deploy to hosted dev FE, collect hosted no-strategy/ready-strategy screenshots, then finalize the task. |
| `AG-DYNUI-PROD-005` | `todo`; no direct branch/PR; task brief still owns strict BFF wiring for proposal accept, grid edit, widget revision, version history, rollback. | No material change. | Implementation has not started; `onAddToTradingRoom` ownership is still an explicit scope question from prior packets. |
| `AG-DYNUI-PROD-006` | `todo`; no direct PR; hosted FE still predates PR #171 and PR #173. | No material parent change. | Cannot run the full hosted Winner Branch E2E until the deploy contains `PROD-002`, `PROD-003`, and `PROD-005` work. |

Practical meaning for parent owner `Codex`: `PROD-003` has moved from
"engineering work not yet represented in standalone repo" to "green PR waiting
for human/reviewer gate." That is useful new information, but it does not
unblock the full parent hosted E2E. The critical path is now:

1. merge and deploy PR #171 (`PROD-002`);
2. review, merge, and deploy PR #173 (`PROD-003`);
3. start/merge/deploy `PROD-005` for the strict BFF-backed V11 workflow;
4. only then author/run `PROD-006` hosted desktop/mobile E2E against the
   deployed FE + live BFF and record evidence.

An earlier hosted checkpoint after #171/#173 merge could still prove the
standalone shell/default-entry portion, but it would not satisfy the full
`PROD-006` acceptance without `PROD-005`.

---

## 5. BFF And Frontend Handoff Notes

The original packet's route inventory remains accurate:

- BFF already exposes proposal create/read/accept, workspace read/patch,
  views/widgets mutations, widget-revision propose/accept including
  `keep_original_add_modified_copy`, version list, and rollback routes.
- The in-tree `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` in this
  pantheon worktree still only exports `getTradingRoom`,
  `getTradingRoomStrategy`, `listDecisionEvents`, `getDecisionEvent`, and
  `decideOnEvent`.
- The standalone `execute-plans` repo is the delivery frontend; PR #173 only
  changes default-entry/routing files and explicitly says it is not changing
  the V11 workspace proposal/grid-editor/widget-revision flow.
- No new Agora hosted E2E spec exists under `execute-plans/e2e/`; `13-agora.spec.ts`
  remains the only Agora Playwright spec visible in this worktree.

For `PROD-006`, keep the parent E2E assertions from the original packet:
desktop/mobile screenshots, no legacy empty shell, explicit
`keep_original_add_modified_copy`, version list appends, rollback creates a new
forward-history version, and no order/capital/broker/RuntimeBinding/Management
controls or calls.

---

## 6. Recommendation For Reviewer And Parent Owner

1. Treat this follow-up as justified by the `PROD-003` PR #173 trigger, not as
   another zero-delta mechanical re-check.
2. Update the mental model from `PROD-003 needs reimplementation` to
   `PROD-003 reimplementation PR exists and is green, but unreviewed,
   unmerged, undeployed, and not screenshot-proven`.
3. Do not start or close the full parent hosted E2E on PR #173 alone. Hosted
   FE still points at `dd597405...`, and `PROD-005` is still not started.
4. If another `FOLLOWUP-6` is considered, require a stronger trigger than
   underutilization: PR #171 or #173 merged, hosted `deployment.json` commit
   changed, `PROD-005` opened a PR, or parent `PROD-006` status/branch changed.
5. If Human/Ops wants an earlier proof slice, run it as a partial hosted
   checkpoint after PR #171/#173 deploy, explicitly excluding proposal/grid
   edit/widget revision/version/rollback acceptance until `PROD-005` lands.

---

## 7. Reviewer Handoff

Reviewer (`Codex2`) should verify:

1. This packet is support-only and made no change to canonical truth, BFF
   runtime, registry/governance code, or frontend code.
2. §2/§3 correctly distinguishes the one new trigger (`PROD-003` PR #173)
   from unchanged blockers (`PROD-002` PR #171, hosted deployment, `PROD-005`,
   parent `PROD-006`).
3. §4's updated readiness view is accurate and does not imply PR #173 is
   enough to unblock the full hosted Winner Branch E2E.
4. §5 keeps the original BFF route inventory as support-only guidance and does
   not redefine the canonical contract.

Recommended reviewer approval command:

```bash
AI_NAME=Codex2 REVIEW_FILE=support/sidecars/AG-DYNUI-PROD-006/AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md \
  REVIEW_NOTES_ZH="Support-only follow-up 5 核准：此 packet 沒有修改 canonical truth/runtime/frontend code；正確更新 FOLLOWUP-4 後的新觸發事件，AG-DYNUI-PROD-003 已有 standalone execute-plans PR #173 且 clean/mergeable/integration-gate green，但仍未 review/merge/deploy/截圖；同時確認 PROD-002 PR #171 仍 open、hosted FE 仍是 dd597405、PROD-005 與 parent PROD-006 仍無直接 PR，因此 full hosted Winner Branch E2E 仍不能視為 unblocked。" \
  ./scripts/ai-status.sh approve AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-5 \
  "Support-only AG-DYNUI-PROD-006 follow-up 5 approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh reopen AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-5 \
  "Describe the factual correction or missing handoff detail needed before approval."
```

---

## 8. Verification Performed For This Sidecar

Commands run from this sidecar worktree unless an absolute path is shown:

```bash
git status -sb
git branch --show-current
git remote -v
git fetch origin dev
git merge-base --is-ancestor origin/dev HEAD

AI_NAME=Codex python3 scripts/ai_status.py show AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-5
AI_NAME=Codex python3 scripts/ai_status.py show AG-DYNUI-PROD-006
AI_NAME=Codex python3 scripts/ai_status.py show AG-DYNUI-PROD-002
AI_NAME=Codex python3 scripts/ai_status.py show AG-DYNUI-PROD-003
AI_NAME=Codex python3 scripts/ai_status.py show AG-DYNUI-PROD-004
AI_NAME=Codex python3 scripts/ai_status.py show AG-DYNUI-PROD-005

gh pr view 171 --repo ajoe734/execute-plans --json number,title,state,mergeable,mergeStateStatus,statusCheckRollup,autoMergeRequest,reviews,headRefName,baseRefName,url,mergedAt
gh pr view 173 --repo ajoe734/execute-plans --json number,title,state,mergeable,mergeStateStatus,statusCheckRollup,autoMergeRequest,reviews,headRefName,baseRefName,url,mergedAt,commits,changedFiles,additions,deletions
gh pr diff 173 --repo ajoe734/execute-plans --name-only
gh pr list --repo ajoe734/execute-plans --head task/AG-DYNUI-PROD-005 --state all --json number,title,state,headRefName,url,mergedAt --limit 20
gh pr list --repo ajoe734/execute-plans --head task/AG-DYNUI-PROD-006 --state all --json number,title,state,headRefName,url,mergedAt --limit 20
gh pr list --repo ajoe734/pantheon --head task/AG-DYNUI-PROD-005 --state all --json number,title,state,headRefName,url,mergedAt --limit 20
gh pr list --repo ajoe734/pantheon --head task/AG-DYNUI-PROD-006 --state all --json number,title,state,headRefName,url,mergedAt --limit 20
gh pr view 2888 --repo ajoe734/pantheon --json number,title,state,mergedAt,mergeCommit,headRefName,baseRefName,url
gh pr view 2891 --repo ajoe734/pantheon --json number,title,state,mergedAt,mergeCommit,headRefName,baseRefName,url
git ls-remote --heads origin 'task/AG-DYNUI-PROD-006*' 'task/AG-DYNUI-PROD-003*' 'task/AG-DYNUI-PROD-005*'

curl -sS --max-time 10 https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json
curl -sS --max-time 10 https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health
rg -n "AG-DYNUI-PROD|pull/171|pull/173|#171|#173|PR #171|PR #173" /home/lupin/code/pantheon/.orchestrator/approval-queue.json

grep -n "^export" execute-plans/src/lib/bff-v1/agora/tradingRoom.ts
git log --oneline -5 -- services/control-plane/bff/agora/trading_room/router.py execute-plans/src/lib/bff-v1/agora/tradingRoom.ts execute-plans/playwright.config.ts
grep -n "workspaces/{workspace_id}/versions\|versions/{version_id}/rollback\|widget-revision-proposals\|trading-room/proposals" services/control-plane/bff/agora/trading_room/router.py
ls execute-plans/e2e
git -C /home/lupin/code/execute-plans status -sb
```

`gh pr diff 173 --repo ajoe734/execute-plans --stat` was also attempted, but
the installed `gh` does not support `--stat` for `pr diff`; changed-file count
and additions/deletions above come from `gh pr view 173`.

No runtime, canonical, registry, governance, frontend, or BFF implementation
files were changed by this sidecar.
