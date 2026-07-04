# AG-DYNUI-PROD-005 BFF and Frontend Handoff Packet - Follow-up 9

| Field | Value |
|---|---|
| Parent task | `AG-DYNUI-PROD-005` |
| Parent title | Close Agora dynamic workflow wiring |
| Parent owner / reviewer | `Claude` / `Codex2` |
| Sidecar task | `AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-9` |
| Sidecar owner / reviewer | `Claude2` / `Claude` |
| Prior sidecars | `AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF` (`done`, PR #2866 + #2870), `FOLLOWUP-2` (`done`, PR #2876 + #2878), `FOLLOWUP-3` (`done`, PR #2880 + #2881), `FOLLOWUP-4` (`done`, PR #2893), `FOLLOWUP-5` (support packet present), `FOLLOWUP-6` (support packet present, recommended stopping), `FOLLOWUP-7` (support packet present, recommended stopping, flagged dispatch-policy root cause), `FOLLOWUP-8` (support packet present, fifth stop-churn confirmation) |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-04` |
| Mutates canonical | `false` |
| Dispatch reason | `owned_ready_dispatch` (sidecar `auto_created_by: supervisor-underutilization`) |

This is a support artifact only. It does not change canonical truth, L1
contracts, BFF runtime code, `execute-plans` frontend code, registry behavior,
or governance behavior. The parent owner (`Claude`) and reviewer (`Codex2`)
decide whether and how to absorb any of the packet series into the mainline
`AG-DYNUI-PROD-005` implementation.

---

## 1. What Changed Since Follow-up 8 (Unlike Follow-ups 5-8, A Trigger Fired)

Follow-ups 4 through 8 each confirmed that none of the four stated trigger
conditions had changed and recommended pausing this sidecar lane. This
packet is different: **one of the four trigger conditions actually fired**.

| Trigger condition (from follow-up 1) | Status as of follow-up 8 | Status now (follow-up 9) |
|---|---|---|
| Parent owner starts/requests a fresh handoff re-check | No | No |
| `execute-plans` PR #171 or #173 merges or changes state materially | Both `OPEN` | **Both `MERGED`** |
| Parent task brief changes scope | No | No |
| Trading Room workspace surface receives a new implementation PR | No | No (see §3) |

Alongside the PR merges, the dependency chain also advanced:

- `AG-DYNUI-PROD-003` moved from `review_approved` (follow-up 8) to
  **`done`** (archived at `2026-07-04T12:39:46Z`; execute-plans PR #173
  merged, hosted screenshot evidence captured, pantheon PR #2955 merged
  into `dev`).
- `AG-DYNUI-PROD-002` is still `review_approved`, but picked up a fresh,
  independent re-verification review note
  (`last_update: 2026-07-04T12:39:20Z`): PR #171 (commit `67c0b048`)
  independently re-run — 118 files / 1102 Vitest tests, `tsc --noEmit`,
  `npm run build`, and `eslint` all pass. The reviewer explicitly deferred
  the hosted desktop/mobile screenshot acceptance item to
  `AG-DYNUI-PROD-006` (the wave-3 hosted E2E gate) and told the owner not
  to run `done` until that hosted proof lands.

So this dispatch was not a no-op repeat of follow-ups 5-8: it correctly
caught a real change in the dependency graph. The rest of this packet
re-verifies whether that change touched the actual `AG-DYNUI-PROD-005`
implementation gap (§2-3), and it did not.

---

## 2. Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates ownership and lifecycle; support packets do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/ag_dynui_prod_005_sidecar_bff_handoff_followup_9.md` | Task is support-only, owner `Claude2`, reviewer `Claude`; artifact target is this packet. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support docs require explicit scoped commits; unrelated dirty files are blockers. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo changes require task commit, PR/merge, and `scripts/ai-status.sh done` after reviewer approval and merge. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-9` | Active sidecar is `in_progress`, owner `Claude2`, reviewer `Claude`, depends on `AG-DYNUI-PROD-003` (now archived `done`) and `AG-DYNUI-PROD-004` (archived `done`), mutates canonical `false`, `auto_created_by: supervisor-underutilization`. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-005` | Parent remains `todo`, owner `Claude`, reviewer `Codex2`, `last_update: 2026-07-04T00:09:32Z` — identical to the value reported in follow-ups 2 through 8; scope and acceptance are unchanged. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-002` | Still `review_approved`, but `last_update` advanced to `2026-07-04T12:39:20Z` with a new independent re-verification review note; hosted screenshots explicitly deferred to `AG-DYNUI-PROD-006`. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-003` | Now archived `done` (`terminal_outcome: completed`); execute-plans PR #173 merged, pantheon PR #2955 merged into `dev` (`a4138d663`, ancestor of `3ff65b566`). |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-004` | Archived `done` (unchanged from follow-up 8). |
| Prior packets in `support/sidecars/AG-DYNUI-PROD-005/` | Original packet plus follow-ups 2-8 already contain the full route inventory, UI gap matrix, workshop handoff scoping question, dependency-gate characterization, and repeated stop-churn recommendation. |
| Focused greps against `execute-plans/src` and `services/control-plane` (mirrored copy inside this pantheon worktree, currently at commit `eab6e0cf`, the merged `AG-DYNUI-PROD-003` head) | No drift in the `AG-DYNUI-PROD-005`-scoped implementation surface despite the PR #171/#173 merges; see §3. |
| GitHub checks via `gh pr view/list` and `git ls-remote` | Parent `task/AG-DYNUI-PROD-005` PR/branch still does not exist in Pantheon or execute-plans. Dependency PRs #171 and #173 are now `MERGED` (both previously `OPEN`), still zero reviews, `autoMergeRequest: null`, both with a `SUCCESS` `integration-gate` check. |

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned, per the task-scoped read-order instruction.

---

## 3. Re-verification: The `AG-DYNUI-PROD-005` Implementation Gap Is Unchanged

The PR #171/#173 merges landed the `AG-DYNUI-PROD-002` (standalone shell)
and `AG-DYNUI-PROD-003` (default dynamic entry) scopes, not the
`AG-DYNUI-PROD-005` V11 dynamic-workflow wiring. Re-running the same checks
from follow-ups 7 and 8 against the now-merged code confirms no material
change to the gap this parent task must still close:

| Check | Current result |
|---|---|
| Parent task state | `AG-DYNUI-PROD-005` remains `todo` with the same `last_update` (`2026-07-04T00:09:32Z`) reported by follow-ups 2 through 8. |
| Parent branch / PR | `git ls-remote --heads origin 'task/AG-DYNUI-PROD-005'` returns no branch. `gh pr list --repo ajoe734/pantheon --head task/AG-DYNUI-PROD-005 --state all` and the same command against `ajoe734/execute-plans` both return `[]`. |
| Parent commit subject | `git log --all --format='%h %s' \| rg '^\\w+ AG-DYNUI-PROD-005:' \| rg -v 'SIDECAR'` returns no matches. |
| Dependency PR #171 | Now `MERGED` (was `OPEN` through follow-up 8). Head `task/AG-DYNUI-PROD-002-agora-standalone-shell-compliant`, `reviews: []`, `autoMergeRequest: null`, `integration-gate` `SUCCESS` at `2026-07-04T01:16:11Z` (same check timestamp as follow-up 8 — the merge itself did not re-run the check). |
| Dependency PR #173 | Now `MERGED` (was `OPEN` through follow-up 8). Head `task/AG-DYNUI-PROD-003-default-route-dynamic-entry`, `reviews: []`, `autoMergeRequest: null`, `integration-gate` `SUCCESS` at `2026-07-04T03:28:21Z` (same check timestamp as follow-up 8). |
| Component mounting | `DashboardProposalPreview`, `WidgetRevisionDrawer`, and `DashboardChangeLog` still appear only in their own definition/export files; none is mounted in the app flow. |
| Grid persistence | `TradingRoomPage.tsx` still wires the grid editor with `onWidgetRemove={() => {}}`, `onWidgetAdd={() => {}}`, and `onWidgetChartChange={() => {}}`; `onPlacementsChange` is still the only non-empty callback and remains local-state-only. |
| V11 workspace clients | Grep for `trading-room/workspaces`, `trading-room/proposals`, `widget-revision-proposals`, `getTradingRoomWorkspace`, and `workspaceId` outside tests/types returns no frontend client implementation. |
| Workshop to Trading Room handoff | `agora-main.tsx` still renders `StrategyWorkshopPage` with only `workshopId` — no `onAddToTradingRoom` prop is passed — while `StrategyWorkshopPage`'s `onAddToTradingRoom` prop is still declared and exercised only in its own isolated test. The scoping question raised in follow-up 2 remains undecided by the parent brief. |
| Widget allowlist/blocklist | Registry still has 42 entries. Backend `_FORBIDDEN_INTERACTIONS` (now present in both `trading_room/router.py` and `dashboard/router.py`) and frontend `BLOCKED_INTERACTION_KINDS` are still present and functionally unchanged. |
| SSE stream | `GET /bff/agora/trading-room/stream` remains a self-documented stub in `trading_room/router.py`, referenced only by route-listing test coverage, not by a real streaming implementation. |
| Dependency status | `AG-DYNUI-PROD-002` remains `review_approved` (hosted screenshots deferred to `AG-DYNUI-PROD-006`, per its own reviewer note). `AG-DYNUI-PROD-003` is now `done`. `AG-DYNUI-PROD-004` remains `done`. None of this changes `AG-DYNUI-PROD-005`'s own scope or unblocks it beyond what its `depends_on` list already reflects (both `AG-DYNUI-PROD-003` and `AG-DYNUI-PROD-004` are now formally closed; `AG-DYNUI-PROD-002` is still only `review_approved`, so the full dependency set is not yet all-`done`). |

No runtime, frontend, BFF, registry, governance, canonical, or contract file
was changed by this sidecar.

---

## 4. Reviewer Guidance

This ninth packet has one genuine new contribution beyond follow-up 8: it
confirms that the PR #171/#173 merges (a trigger condition that had been
open since follow-up 1) did **not** touch the `AG-DYNUI-PROD-005`-scoped
implementation surface, and it records that `AG-DYNUI-PROD-003` closed to
`done` while `AG-DYNUI-PROD-002` still awaits hosted proof from
`AG-DYNUI-PROD-006`.

Recommended reviewer disposition:

1. approve this packet if the re-checks in §3 are accurate;
2. note that `AG-DYNUI-PROD-005`'s `depends_on` list (`AG-DYNUI-PROD-002`,
   `AG-DYNUI-PROD-003`, `AG-DYNUI-PROD-004`) is not yet fully `done` —
   `AG-DYNUI-PROD-002` is still `review_approved` pending hosted screenshot
   evidence from `AG-DYNUI-PROD-006` — so `AG-DYNUI-PROD-005` implementation
   start is still gated on that, independent of this sidecar;
3. since the actual `AG-DYNUI-PROD-005` gap did not move, the stop-churn
   recommendation from follow-ups 4-8 still holds for future *identical*
   idle-driven dispatches; however this packet demonstrates the trigger
   conditions are being checked correctly, so no change to sidecar-lane
   policy is being requested beyond what follow-up 7 already flagged;
4. route parent attention to the existing reader's guide instead of asking
   for another inventory:
   - original packet: full BFF route inventory, frontend gap matrix, operator
     journeys, and suggested client methods;
   - follow-up 2: Workshop -> Trading Room `onAddToTradingRoom` gap and
     scoping ambiguity;
   - follow-up 3: dependency chain blocked on two independent human gates;
   - follow-up 4: approved no-drift closeout and first stop-churn
     recommendation;
   - follow-up 5: no-drift and stop-churn still hold;
   - follow-up 6: no-drift and stop-churn still hold after the sixth
     dispatch;
   - follow-up 7: no-drift and stop-churn still hold after the seventh
     dispatch; dispatch-policy root cause flagged for the chair/supervisor
     owner;
   - follow-up 8: no-drift and stop-churn still hold after the eighth
     dispatch; recommended pausing this sidecar lane's re-dispatch entirely;
   - this packet: PR #171/#173 merged and `AG-DYNUI-PROD-003` closed to
     `done`, but the `AG-DYNUI-PROD-005`-scoped implementation gap is still
     unchanged; `AG-DYNUI-PROD-002` remains the last open dependency.

Recommended reviewer approval command:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/AG-DYNUI-PROD-005/AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-9.md \
  REVIEW_NOTES_ZH="Support-only follow-up 9 核准：本次派工實際捕捉到一個 trigger 條件變化（execute-plans PR #171/#173 由 open 轉為 merged，AG-DYNUI-PROD-003 轉為 done，AG-DYNUI-PROD-002 仍 review_approved 但已補上獨立重驗證並將 hosted screenshot 明確 defer 給 AG-DYNUI-PROD-006）；重新驗證確認這兩個 merge 屬於 002/003 範疇，未觸及 AG-DYNUI-PROD-005 本身的 V11 dynamic workflow 缺口（未掛載元件、no-op grid callbacks、缺 V11 workspace clients、onAddToTradingRoom 仍未串接、allowlist/blocklist 與 SSE stub 均未變動）。Parent AG-DYNUI-PROD-005 仍為 todo，無 parent branch/PR/commit。未修改 canonical truth 或 runtime 檔案。" \
  ./scripts/ai-status.sh approve AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-9 \
  "Support-only AG-DYNUI-PROD-005 follow-up 9 approved; confirms PR #171/#173 merges and AG-DYNUI-PROD-003 closeout did not change the AG-DYNUI-PROD-005-scoped implementation gap."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-9 \
  "Describe the factual correction, ownership-boundary issue, or missing handoff detail needed before approval."
```

---

## 5. Validation Run

Commands run from this sidecar worktree:

```bash
git status -sb
git branch --show-current

AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-9
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-005
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-002
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-003
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-004

gh pr view 171 --repo ajoe734/execute-plans --json number,state,mergeable,mergeStateStatus,reviews,headRefName,url,statusCheckRollup,autoMergeRequest
gh pr view 173 --repo ajoe734/execute-plans --json number,state,mergeable,mergeStateStatus,reviews,headRefName,url,statusCheckRollup,autoMergeRequest
gh pr list --repo ajoe734/pantheon --head task/AG-DYNUI-PROD-005 --state all --json number,title,state,headRefName,url
gh pr list --repo ajoe734/execute-plans --head task/AG-DYNUI-PROD-005 --state all --json number,title,state,headRefName,url
git ls-remote --heads origin 'task/AG-DYNUI-PROD-005'
git log --all --format='%h %s' | rg '^\\w+ AG-DYNUI-PROD-005:' | rg -v 'SIDECAR'

rg -n "DashboardProposalPreview|WidgetRevisionDrawer|DashboardChangeLog" execute-plans/src -g '*.tsx' -g '!*.test.*'
rg -n "onWidgetAdd|onWidgetRemove|onWidgetChartChange|onPlacementsChange" execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx
rg -n "trading-room/workspaces|trading-room/proposals|widget-revision-proposals|getTradingRoomWorkspace|workspaceId" execute-plans/src -g '*.ts' -g '*.tsx' -g '!*.test.*' -g '!types.ts'
rg -n "StrategyWorkshopPage" execute-plans/src/entries/agora-main.tsx
rg -n "onAddToTradingRoom" execute-plans/src/entries/agora-main.tsx
jq '[keys, (.entries | length)]' services/control-plane/specs/agora/widget_registry.v1.json
rg -n "_FORBIDDEN_INTERACTIONS|BLOCKED_INTERACTION_KINDS" services/control-plane/bff/agora execute-plans/src/agora
rg -n "trading-room/stream" services/control-plane/bff/agora
```

Expected non-zero grep behavior: the V11 workspace-client grep and the
`agora-main.tsx` `onAddToTradingRoom` grep both return no matches; that is
the expected result confirming the client-implementation and
workshop-handoff gaps remain. The parent commit-subject grep also returns
no matches; that is the expected result confirming no parent task
implementation commit exists.
