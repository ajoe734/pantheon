# AG-FE-DYNUI-004 Sidecar Review Packet

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-DYNUI-004-SIDECAR-REVIEW` |
| Helper parent | `AG-FE-DYNUI-004` |
| Helper kind | `review_packet` |
| Parent title | Widget adjustment drawer and before-after revision flow |
| Parent owner / reviewer | `Codex2` / `Codex` as of status readback |
| Sidecar owner / reviewer | `Codex` / `Codex2` |
| Date | `2026-06-29` |
| Mutates canonical truth | `false` |
| Status | Ready for `Codex2` sidecar review; parent remains in normal review |

This is a support-only review packet. It summarizes current parent evidence,
review gates, and handoff notes for `AG-FE-DYNUI-004`. It does not approve the
parent implementation, merge the parent PR, edit canonical truth, or change any
runtime, registry, governance, BFF, backend, OpenAPI, generated type, or
execute-plans source file.

## 1. Scope Boundary

| Surface | Boundary |
|---|---|
| This sidecar owns | Review packet and evidence summary for parent review routing. |
| This sidecar does not own | Parent approval, parent merge, canonical truth, runtime code, frontend code, backend contracts, generated types, governance logic, or downstream visual/E2E scope. |
| Parent implementation evidence | execute-plans PR `#84`, head `a4ccb61543b37ebb6ce35b91e3b2b7c558b3c460`, base `dev`. |
| Prior support packets | The archived acceptance packet and follow-ups remain the checklist and dependency background. This packet only refreshes review evidence for PR `#84`. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## 2. Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates task work; support packets cannot override L1/L2 truth. |
| `.orchestrator/task-briefs/ag_fe_dynui_004_sidecar_review.md` | Scope is review packet, evidence summary, and reviewer handoff only; canonical/runtime changes are out of scope. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support/docs changes must be committed narrowly with explicit scope. |
| `.orchestrator/skills/task-closeout-finalization.md` | Final `done` is owner closeout after review approval and merged task PR, not a simple status flip. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DYNUI-004-SIDECAR-REVIEW` | Sidecar is active `in_progress`, owner `Codex`, reviewer `Codex2`, helper parent `AG-FE-DYNUI-004`, artifact path is this packet, and `mutates_canonical` is `false`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DYNUI-004` | Parent is active `review`, owner `Codex2`, reviewer `Codex`; next action points to execute-plans PR `#84`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DYNUI-004-SIDECAR-ACCEPTANCE*` | Prior support packets are archived `done`; they do not approve the parent implementation. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DYNUI-005`, `AG-E2E-DYNUI-001` | Final design-pack visual parity and full Winner Branch E2E remain downstream. |
| `support/sidecars/AG-FE-DYNUI-004/AG-FE-DYNUI-004-SIDECAR-ACCEPTANCE.md` | Main 30-point acceptance checklist remains the primary behavior gate for widget revision drawer review. |
| `support/sidecars/AG-FE-DYNUI-004/AG-FE-DYNUI-004-SIDECAR-ACCEPTANCE-FOLLOWUP-2.md` | Prior evidence packet recorded PR `#83` merged/green and the unresolved execute-plans `dev` vs `main` composition note. |
| `support/sidecars/AG-FE-DYNUI-004/AG-FE-DYNUI-004-SIDECAR-ACCEPTANCE-FOLLOWUP-3.md` | Latest prior support packet warned not to use a dirty local checkout as parent delivery proof and kept parent approval separate. |
| `gh pr view 84 --repo ajoe734/execute-plans --json ...` | PR `#84` is open, non-draft, merge state `CLEAN`, base `dev`, head `task/AG-FE-DYNUI-004` at `a4ccb61543b37ebb6ce35b91e3b2b7c558b3c460`, title `AG-FE-DYNUI-004: finish widget revision drawer`. |
| `gh pr checks 84 --repo ajoe734/execute-plans` | `integration-gate` passed in `7m24s`. |
| `gh run view 28372336773 --repo ajoe734/execute-plans --json ...` | Workflow completed `success` for head `a4ccb61543b37ebb6ce35b91e3b2b7c558b3c460`; lint, unit/integration tests, build, contract drift, BFF probes, Playwright E2E, evidence upload, and PR comment steps succeeded. |
| `git ls-remote --heads https://github.com/ajoe734/execute-plans.git task/AG-FE-DYNUI-004 main dev` | Remote task branch exists at `a4ccb61543b37ebb6ce35b91e3b2b7c558b3c460`; `dev` is `a95d5d7855d31c0b93ab6b6cb4523b69669a3797`; `main` is `64a963119e85f2e91efbedbd83c4fbd97c7c2e20`. |
| `git -C /home/lupin/code/execute-plans status -sb` | Local frontend checkout is clean on `task/AG-FE-DYNUI-004...origin/task/AG-FE-DYNUI-004`. |
| `git -C /home/lupin/code/execute-plans diff origin/dev...HEAD --stat` | PR-local diff is 4 files, 500 insertions, 13 deletions. |
| `git -C /home/lupin/code/execute-plans diff --check origin/dev...HEAD` | No whitespace errors were reported for the parent PR diff. |
| PR diff safety grep | No forbidden safety terms were added in the PR `#84` diff. A broad tree grep still finds pre-existing safety guard/test/registry strings. |
| `git -C /home/lupin/code/execute-plans merge-base --is-ancestor origin/main origin/dev` | Returned non-zero; the `dev` vs `main` composition note remains unresolved. |

## 3. Current Parent Evidence Snapshot

| Evidence | Current state | Review consequence |
|---|---|---|
| Parent status | `AG-FE-DYNUI-004` is in `review`; owner `Codex2`, reviewer `Codex`. | Parent still needs the normal parent review action. This sidecar is not that approval. |
| Parent PR | execute-plans PR `#84` is open, non-draft, `CLEAN`, and based on `dev`. | Reviewer can review and merge PR `#84`; it is not merged yet. |
| Parent commit | `a4ccb61543b37ebb6ce35b91e3b2b7c558b3c460`, subject `AG-FE-DYNUI-004: finish widget revision drawer`. | This is the current parent implementation commit to review. |
| CI gate | `Pantheon FE-BFF Integration Gate / integration-gate` passed in run `28372336773`. | Automated publication and smoke evidence is available. |
| Changed files | `TradingRoomPage.test.tsx`, `WorkspaceWidgetRevisionDrawer.tsx`, `tradingRoom.test.ts`, `tradingRoom.ts`. | Diff is centered on drawer behavior, BFF envelope handling, and focused tests. |
| Local FE checkout | Clean on `task/AG-FE-DYNUI-004` at the PR head. | Local inspection is currently safe, but PR `#84` remains the delivery authority. |
| Delivery base | PR base is `dev`; `origin/main` is still not an ancestor of `origin/dev`. | Parent closeout should explicitly record the dev delivery target and composition plan before downstream visual/E2E work. |

## 4. PR #84 Diff Summary

| File | Review-relevant delta |
|---|---|
| `src/agora/trading-room/WorkspaceWidgetRevisionDrawer.tsx` | Adds fuller widget/workspace/view context rows, durable before/after field diff, typed BFF error mapping for 403/404/412/422/502, disabled-reason submit guard, and error metadata attributes. |
| `src/lib/bff-v1/agora/tradingRoom.ts` | Hardens `extractWidgetRevisionProposal` to accept `data.proposal` envelopes and fail closed when `beforeSpec` or `proposedSpec` is missing. |
| `src/lib/bff-v1/agora/tradingRoom.test.ts` | Adds tests for typed 403/404/412/422 widget revision failures and malformed proposal envelopes. |
| `src/agora/pages/trading-room/TradingRoomPage.test.tsx` | Expands drawer context assertions, checks backend durable diff rendering, cancel no-op behavior, adjust-again fresh proposal behavior, and unsaved-layout disabled proposal submission. |

No sidecar-level blocker was found in this scoped evidence pass. Parent reviewer
still needs to review behavior against the full acceptance checklist before
approving or merging the parent task.

## 5. Parent Review Gate

Use the original sidecar acceptance packet as the full checklist. For the
current PR `#84`, parent review should specifically confirm:

1. Drawer context includes workspace id/version, strategy id/version, view id,
   widget id/title/type/purpose/whyIncluded, data source, query filters/window,
   sort/limit, chart kind/spec, interactions, sensitivity, placement, warnings,
   data availability, and evidence context.
2. Proposal creation remains BFF-helper-only and server-backed; no page-level
   direct fetch, local-only `proposedSpec` proof, direct widget mutation, or
   non-BFF route is introduced.
3. Apply and keep-copy still send idempotency keys and current workspace
   `If-Match` ETag; stale proposal failures do not mutate workspace state.
4. The before/after table uses backend `beforeSpec` and `proposedSpec` once a
   proposal exists, not a mutable local draft.
5. Cancel closes without accepting or mutating the workspace; adjust again does
   not accidentally apply stale proposal ids.
6. Typed 403/404/412/422/502 errors remain useful but do not leak backend
   internals or unrelated workspace/widget details.
7. Existing grid editor behavior remains intact: edit mode, unsaved layout
   guard, save/discard, remove/restore, add/duplicate, change chart, version
   listing, rollback, and personalization display.
8. No unsafe renderer/code paths are introduced: no generated React/JS/HTML
   execution, no `dangerouslySetInnerHTML`, no `eval`, no iframe/script path,
   and no arbitrary data-source URL execution.
9. No order/capital/runtime authority leaks into UI copy or actions.
10. The parent closeout records that PR `#84` targets execute-plans `dev` while
    `origin/main` is not yet proven to contain `origin/dev`.

## 6. Review Risk Notes

| Risk | Current evidence | Suggested reviewer action |
|---|---|---|
| Parent PR is not merged | PR `#84` is open and clean with green checks. | Do not mark parent `done` until PR `#84` merges and parent closeout records the merge commit. |
| `dev` vs `main` composition remains unresolved | `merge-base --is-ancestor origin/main origin/dev` returned non-zero. | Require a clear parent closeout note or follow-up plan for how `dev` delivery composes back to `main`. |
| Drawer diff serializes rich chart/query/interactions | PR adds `stableText(...)` field diff output. | Review for readability and no leakage of internal/provider/broker details in user-visible diff text. |
| Error mapping exposes status/code attributes | UI now includes `data-error-code` and `data-error-status`; user text is mapped. | Confirm user-facing messages are safe and test-only metadata is acceptable. |
| Validation relies on existing helper contracts | CI is green and helper tests were expanded. | Spot-check BFF helper calls still pass idempotency and ETag options in the accept path. |
| Downstream visual/E2E scope could be conflated | `AG-FE-DYNUI-005` and `AG-E2E-DYNUI-001` remain future work. | Keep final visual parity and full Winner Branch E2E out of this parent approval. |

## 7. Handoff Recommendation

For this sidecar:

```bash
AI_NAME=Codex ./scripts/ai-status.sh handoff AG-FE-DYNUI-004-SIDECAR-REVIEW Codex2 \
  "Support-only review packet is ready. It summarizes AG-FE-DYNUI-004 parent PR #84 evidence, run 28372336773 success, scoped diff/risk notes, and reviewer gates without approving parent implementation or changing canonical/runtime files."
```

For parent review, keep the action separate. The parent reviewer should review
execute-plans PR `#84`, decide whether to approve/merge the parent PR, and only
then let the parent owner complete the parent closeout workflow.

## 8. Support-Only Boundary Confirmation

- No L1/L2 canonical policy or architecture document was edited by this
  sidecar.
- No backend schema, OpenAPI, BFF route, runtime, registry, governance, or
  generated type file was changed by this sidecar.
- No execute-plans frontend runtime file was changed by this sidecar.
- The intended support deliverable is this packet:
  `support/sidecars/AG-FE-DYNUI-004/AG-FE-DYNUI-004-SIDECAR-REVIEW.md`.
- The generated task brief is task-scoped state:
  `.orchestrator/task-briefs/ag_fe_dynui_004_sidecar_review.md`.
- This sidecar does not approve the parent implementation.

## 9. Validation Run

Commands run from this Pantheon sidecar worktree unless noted:

```bash
git status -sb
git branch --show-current
git remote -v
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DYNUI-004-SIDECAR-REVIEW
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DYNUI-004
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DYNUI-004-SIDECAR-ACCEPTANCE-FOLLOWUP-3
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DYNUI-004-SIDECAR-ACCEPTANCE-FOLLOWUP-2
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DYNUI-004-SIDECAR-ACCEPTANCE
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DYNUI-005
AI_NAME=Codex ./scripts/ai-status.sh show AG-E2E-DYNUI-001
gh pr view 84 --repo ajoe734/execute-plans --json number,state,title,url,headRefName,baseRefName,headRefOid,mergeCommit,mergedAt,updatedAt,isDraft,mergeStateStatus,statusCheckRollup,reviewDecision,commits,files,author
gh pr checks 84 --repo ajoe734/execute-plans
gh run view 28372336773 --repo ajoe734/execute-plans --json status,conclusion,createdAt,updatedAt,headSha,displayTitle,event,workflowName,url,jobs
git ls-remote --heads https://github.com/ajoe734/execute-plans.git task/AG-FE-DYNUI-004 main dev
git -C /home/lupin/code/execute-plans status -sb
git -C /home/lupin/code/execute-plans branch --show-current
git -C /home/lupin/code/execute-plans rev-parse HEAD origin/dev origin/main
git -C /home/lupin/code/execute-plans diff --stat origin/dev...HEAD
git -C /home/lupin/code/execute-plans diff --name-only origin/dev...HEAD
git -C /home/lupin/code/execute-plans diff --check origin/dev...HEAD
git -C /home/lupin/code/execute-plans diff origin/dev...HEAD -- src/agora/pages/trading-room/TradingRoomPage.test.tsx src/agora/trading-room/WorkspaceWidgetRevisionDrawer.tsx src/lib/bff-v1/agora/tradingRoom.test.ts src/lib/bff-v1/agora/tradingRoom.ts | rg -n "RuntimeBinding|Management|broker|capital|place_order|enable_live|dangerouslySetInnerHTML|eval\\(|new Function|iframe|rawHtml|external script"
git -C /home/lupin/code/execute-plans merge-base --is-ancestor origin/main origin/dev
```

Observed results:

- Pantheon sidecar branch is
  `task/AG-FE-DYNUI-004-SIDECAR-REVIEW`.
- The only pre-edit dirty file was the generated task brief for this sidecar.
- Sidecar is active `in_progress`, owner `Codex`, reviewer `Codex2`, helper
  parent `AG-FE-DYNUI-004`, and support-only.
- Parent is active `review`, owner `Codex2`, reviewer `Codex`; parent PR `#84`
  is not merged yet.
- Prior acceptance sidecars are archived `done` and explicitly do not approve
  parent implementation.
- execute-plans PR `#84` is open, non-draft, clean, and green at
  `a4ccb61543b37ebb6ce35b91e3b2b7c558b3c460`.
- GitHub run `28372336773` completed `success`; lint, unit/integration tests,
  build, contract drift, BFF probes, Playwright E2E, and evidence upload
  succeeded.
- Parent PR diff is limited to 4 files: drawer, BFF helper, and focused tests.
- Parent PR diff has no whitespace errors and no newly added forbidden safety
  terms from the scoped grep.
- execute-plans `origin/main` is still not proven to be an ancestor of
  `origin/dev`.
