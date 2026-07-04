# AG-DYNUI-PROD-006 BFF and Frontend Handoff Packet - Follow-up 6

| Field | Value |
|---|---|
| Parent task | `AG-DYNUI-PROD-006` |
| Parent title | Hosted Winner Branch E2E publish gate |
| Parent owner / reviewer | `Codex` / `Claude2` |
| Sidecar task | `AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` |
| Sidecar owner / reviewer | `Codex` / `Codex2` |
| Prior sidecars | `AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF` (`done`, PR #2869), `-FOLLOWUP-2` (`done`, PR #2879), `-FOLLOWUP-3` (`done`, PR #2882/#2883), `-FOLLOWUP-4` (`done`, PR #2884), `-FOLLOWUP-5` (`done`, PR #2892/#2894) |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-04` |
| Mutates canonical | `false` |

This is a support artifact only. It does not change canonical truth, L1
contracts, BFF runtime code, `execute-plans` frontend code, registry behavior,
or governance behavior. The parent owner (`Codex`) and reviewer (`Claude2`)
decide whether and how to absorb this packet into the mainline closeout.

---

## 1. Why This Follow-up Exists

`FOLLOWUP-5` already identified the only material positive change after
`FOLLOWUP-4`: `AG-DYNUI-PROD-003` now has standalone `execute-plans` PR #173
open and green. That PR is still not reviewed, merged, deployed, or
screenshot-proven.

This `FOLLOWUP-6` was dispatched again by supervisor underutilization. It did
not find a new parent unblocker. Its only new observation is adjacent churn:
`AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` appeared while this packet
was being prepared, then merged through Pantheon PR #2895 at
`2026-07-04T05:24:50Z` (`ff1dae8a45c15080950645e63fce00686ddee599`) and is now
archived `done`. That sidecar's own review note says the `PROD-005` parent
remains `todo`, no parent branch/PR exists, and the workspace-client/UI gaps
have not drifted.

Practical meaning: the readiness picture for `AG-DYNUI-PROD-006` is still
unchanged from `FOLLOWUP-5`. The full hosted Winner Branch E2E remains blocked
on upstream implementation and deploy gates, not on missing BFF handoff
research.

---

## 2. Sources Read And Current Findings

| Source | Finding |
|---|---|
| `.orchestrator/task-briefs/ag_dynui_prod_006_sidecar_bff_handoff_followup_6.md` | Scope is support-only: prepare BFF/frontend handoff materials for `AG-DYNUI-PROD-006`; do not modify canonical truth. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` | Sidecar is `in_progress`, owner `Codex`, reviewer `Codex2`, artifact path is this packet. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-DYNUI-PROD-006` | Parent is still `todo`, owner `Codex`, reviewer `Claude2`, `last_update: 2026-07-04T00:09:32Z`; no parent task PR exists in either repo. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-DYNUI-PROD-002` + `gh pr view 171 --repo ajoe734/execute-plans` | Still `review_approved`; execute-plans PR #171 remains `OPEN`, `MERGEABLE`, `CLEAN`, integration-gate success, zero reviews, no auto-merge request. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-DYNUI-PROD-003` + `gh pr view 173 --repo ajoe734/execute-plans` | Still `review_approved`; execute-plans PR #173 remains `OPEN`, `MERGEABLE`, `CLEAN`, integration-gate success, zero reviews, no auto-merge request. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-DYNUI-PROD-004` | Archived `done`; dependency remains complete. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-DYNUI-PROD-005` | Parent remains `todo`, owner `Claude`, reviewer `Codex2`, unchanged `last_update: 2026-07-04T00:09:32Z`; no direct implementation branch/PR exists. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` | New adjacent sidecar is now archived `done`; review note explicitly confirms no parent implementation drift and recommends stopping additional same-shape BFF handoff churn. |
| `gh pr view 2895 --repo ajoe734/pantheon` | The `PROD-005` follow-up 5 support packet PR is `MERGED` at `2026-07-04T05:24:50Z` with merge commit `ff1dae8a45c15080950645e63fce00686ddee599`; checks are successful. |
| Hosted FE deployment | Still `execute-plans` commit `dd597405e014cc91cf73f4ea2e96a561fcbf9c61`, deployed `20260704T012041Z`; PR #171 and PR #173 are not deployed. |
| Hosted BFF health | `operator-bff` is healthy at version `0.2.0`. |
| Approval queue exact check | Central `/home/lupin/code/pantheon/.orchestrator/approval-queue.json` has no exact `AG-DYNUI-PROD`, `pull/171`, `pull/173`, `#171`, or `#173` match. |
| BFF/frontend inventory re-check | Backend proposal/workspace/widget-revision/version/rollback routes remain present in `router.py`; in-tree `tradingRoom.ts` still exports only the older aggregate/decision-event client functions; `execute-plans/e2e/` still has only `13-agora.spec.ts` for Agora coverage. |
| Standalone execute-plans checkout | `/home/lupin/code/execute-plans` local checkout is dirty and far ahead/behind `origin/dev`; this packet uses GitHub PR state, `origin/dev`, and hosted `deployment.json` as evidence, not that local checkout as a deployment source. |

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned, per the task-scoped read-order instruction.

---

## 3. Delta Since Follow-up 5

| Trigger or watched fact | Current result | Effect on parent readiness |
|---|---|---|
| execute-plans PR #171 merges | **No.** Still open/clean/green/unreviewed. | `PROD-002` remains blocked on human merge approval, then deploy evidence. |
| execute-plans PR #173 merges | **No.** Still open/clean/green/unreviewed. | `PROD-003` remains blocked on review/merge/deploy/screenshots. |
| New hosted dev FE deploy | **No.** `deployment.json` still reports `dd597405...` from `20260704T012041Z`. | Hosted FE still predates `PROD-002` and `PROD-003`. |
| `PROD-005` direct implementation branch/PR | **No.** No direct parent PR in `ajoe734/pantheon` or `ajoe734/execute-plans`; no `task/AG-DYNUI-PROD-005` parent branch on origin. | Full proposal/grid/widget revision/version/rollback E2E remains blocked. |
| `PROD-005` adjacent support sidecar | **Yes.** `FOLLOWUP-5` exists and is now `done`, with PR #2895 merged. | Does not unblock parent; it repeats no-drift and stop-churn guidance. |
| Parent `PROD-006` status/branch | **No.** Parent remains `todo`, no direct implementation branch/PR. | Parent hosted E2E still should not be treated as actionable for full flow. |
| Approval queue signal for PR #171/#173 | **No exact match.** | No queued approval evidence was found from the central queue file. |

---

## 4. Updated Readiness View

| Dependency | Current state | Remaining blocker |
|---|---|---|
| `AG-DYNUI-PROD-002` | `review_approved`; execute-plans PR #171 open, clean, green, zero reviews. Pantheon task-brief PR churn continues, including open PR #2890, but it does not merge the standalone frontend PR. | Human review/merge of PR #171, then hosted deploy and screenshot evidence. |
| `AG-DYNUI-PROD-003` | `review_approved`; execute-plans PR #173 open, clean, green, zero reviews. | Review/merge PR #173, deploy to hosted dev FE, collect hosted no-strategy/ready-strategy screenshots, then finalize. |
| `AG-DYNUI-PROD-005` | Parent still `todo`; no direct implementation branch or PR. Latest `PROD-005` sidecar follow-up is merged and archived `done`, but it is only support/review material. | Start and land strict BFF-backed proposal, grid edit, widget revision, version history, and rollback workflow wiring. |
| `AG-DYNUI-PROD-006` | Parent still `todo`; no direct branch/PR; hosted FE still predates PR #171/#173. | Cannot run the full hosted Winner Branch E2E until the deploy contains `PROD-002`, `PROD-003`, and `PROD-005` work. |

The original BFF route inventory remains usable support guidance. There is
still no evidence that `AG-DYNUI-PROD-006` needs a new BFF route or canonical
contract change before the frontend workflow lands.

---

## 5. Parent Handoff Guidance

For parent owner `Codex`, the critical path is still:

1. merge and deploy execute-plans PR #171 (`PROD-002`);
2. review, merge, and deploy execute-plans PR #173 (`PROD-003`);
3. start, merge, and deploy `PROD-005` for strict BFF-backed V11 workflow
   wiring;
4. only then author/run `PROD-006` hosted desktop/mobile E2E against the
   deployed FE + live BFF and record evidence.

The `PROD-005` support follow-up 5 does not change step 3. It confirms the
same implementation gap still exists. A future `AG-DYNUI-PROD-006` support
follow-up should not be dispatched merely because the worker pool is
underutilized. A follow-up becomes useful only if one of these concrete events
occurs:

- PR #171 or #173 merges;
- hosted `deployment.json` changes away from `dd597405...`;
- a real `PROD-005` implementation branch/PR appears;
- parent `AG-DYNUI-PROD-006` status/branch changes;
- a BFF route or frontend workflow surface actually changes.

---

## 6. Reviewer Handoff

Reviewer (`Codex2`) should verify:

1. This packet is support-only and made no change to canonical truth, BFF
   runtime, registry/governance code, or frontend code.
2. §2/§3 correctly distinguish the only new observation (`PROD-005`
   `FOLLOWUP-5` sidecar/merged PR #2895) from unchanged parent blockers.
3. §4 does not treat support-sidecar progress as parent implementation
   progress.
4. §5's stop-churn recommendation is consistent with both `PROD-006`
   `FOLLOWUP-5` and `PROD-005` `FOLLOWUP-5` review notes.

Recommended reviewer approval command:

```bash
AI_NAME=Codex2 REVIEW_FILE=support/sidecars/AG-DYNUI-PROD-006/AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.md \
  REVIEW_NOTES_ZH="Support-only follow-up 6 核准：此 packet 沒有修改 canonical truth/runtime/frontend code；重新核實 PROD-002 PR #171 與 PROD-003 PR #173 仍 open/clean/green/零 reviews，hosted FE 仍是 dd597405，PROD-005 parent 仍 todo 且無 direct implementation PR，parent PROD-006 仍 todo。唯一新增事實是 PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-5 已透過 PR #2895 merge 並歸檔 done，但那只是同質 support packet，不解除 parent hosted E2E blocker；建議停止無實質觸發的同質 follow-up dispatch。" \
  ./scripts/ai-status.sh approve AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-6 \
  "Support-only AG-DYNUI-PROD-006 follow-up 6 approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh reopen AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-6 \
  "Describe the factual correction or missing handoff detail needed before approval."
```

---

## 7. Verification Performed For This Sidecar

Commands run from this sidecar worktree unless an absolute path is shown:

```bash
git status -sb
git branch --show-current
git remote -v
git fetch origin dev
git merge-base --is-ancestor origin/dev HEAD

AI_NAME=Codex python3 scripts/ai_status.py show AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-6
AI_NAME=Codex python3 scripts/ai_status.py show AG-DYNUI-PROD-006
AI_NAME=Codex python3 scripts/ai_status.py show AG-DYNUI-PROD-002
AI_NAME=Codex python3 scripts/ai_status.py show AG-DYNUI-PROD-003
AI_NAME=Codex python3 scripts/ai_status.py show AG-DYNUI-PROD-004
AI_NAME=Codex python3 scripts/ai_status.py show AG-DYNUI-PROD-005
AI_NAME=Codex python3 scripts/ai_status.py show AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-5
AI_NAME=Codex python3 scripts/ai_status.py show AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-5

gh pr view 171 --repo ajoe734/execute-plans --json number,title,state,mergeable,mergeStateStatus,statusCheckRollup,autoMergeRequest,reviews,headRefName,baseRefName,url,mergedAt,commits,changedFiles,additions,deletions
gh pr view 173 --repo ajoe734/execute-plans --json number,title,state,mergeable,mergeStateStatus,statusCheckRollup,autoMergeRequest,reviews,headRefName,baseRefName,url,mergedAt,commits,changedFiles,additions,deletions
gh pr view 2895 --repo ajoe734/pantheon --json number,title,state,mergeable,mergeStateStatus,statusCheckRollup,reviews,headRefName,baseRefName,url,mergedAt,autoMergeRequest,commits,changedFiles,additions,deletions
gh pr list --repo ajoe734/execute-plans --head task/AG-DYNUI-PROD-005 --state all --json number,title,state,headRefName,baseRefName,url,mergedAt,mergeCommit --limit 20
gh pr list --repo ajoe734/execute-plans --head task/AG-DYNUI-PROD-006 --state all --json number,title,state,headRefName,baseRefName,url,mergedAt,mergeCommit --limit 20
gh pr list --repo ajoe734/pantheon --head task/AG-DYNUI-PROD-005 --state all --json number,title,state,headRefName,baseRefName,url,mergedAt,mergeCommit --limit 20
gh pr list --repo ajoe734/pantheon --head task/AG-DYNUI-PROD-006 --state all --json number,title,state,headRefName,baseRefName,url,mergedAt,mergeCommit --limit 20
git ls-remote --heads origin 'task/AG-DYNUI-PROD-006*' 'task/AG-DYNUI-PROD-005*' 'task/AG-DYNUI-PROD-003*'

curl -sS --max-time 10 https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json
curl -sS --max-time 10 https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health
rg -n "AG-DYNUI-PROD|pull/171|pull/173|#171([^0-9]|$)|#173([^0-9]|$)|PR #171([^0-9]|$)|PR #173([^0-9]|$)" /home/lupin/code/pantheon/.orchestrator/approval-queue.json

grep -n "^export" execute-plans/src/lib/bff-v1/agora/tradingRoom.ts
grep -n "workspaces/{workspace_id}/versions\|versions/{version_id}/rollback\|widget-revision-proposals\|trading-room/proposals" services/control-plane/bff/agora/trading_room/router.py
git log --oneline -5 -- services/control-plane/bff/agora/trading_room/router.py execute-plans/src/lib/bff-v1/agora/tradingRoom.ts execute-plans/playwright.config.ts
ls execute-plans/e2e
git -C /home/lupin/code/execute-plans fetch origin dev
git -C /home/lupin/code/execute-plans log --oneline -5 origin/dev
git -C /home/lupin/code/execute-plans status -sb
```

No runtime, canonical, registry, governance, frontend, or BFF implementation
files were changed by this sidecar.
