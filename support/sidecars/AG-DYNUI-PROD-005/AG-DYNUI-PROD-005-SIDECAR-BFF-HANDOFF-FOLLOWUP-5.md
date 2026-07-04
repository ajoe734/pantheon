# AG-DYNUI-PROD-005 BFF and Frontend Handoff Packet - Follow-up 5

| Field | Value |
|---|---|
| Parent task | `AG-DYNUI-PROD-005` |
| Parent title | Close Agora dynamic workflow wiring |
| Parent owner / reviewer | `Claude` / `Codex2` |
| Sidecar task | `AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` |
| Sidecar owner / reviewer | `Codex2` / `Claude` |
| Prior sidecars | `AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF` (`done`, PR #2866 + #2870), `FOLLOWUP-2` (`done`, PR #2876 + #2878), `FOLLOWUP-3` (`done`, PR #2880 + #2881), `FOLLOWUP-4` (`done`, PR #2893) |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-04` |
| Mutates canonical | `false` |
| Dispatch reason | `owned_ready_dispatch` |

This is a support artifact only. It does not change canonical truth, L1
contracts, BFF runtime code, `execute-plans` frontend code, registry behavior,
or governance behavior. The parent owner (`Claude`) and reviewer (`Codex2`)
decide whether and how to absorb this packet into the mainline
`AG-DYNUI-PROD-005` implementation.

---

## 1. Why This Follow-up Exists

The supervisor created a fifth BFF handoff sidecar after follow-up 4 had
already recommended stopping mechanical sidecar churn unless one of these
facts changed:

1. the parent owner starts or requests a fresh handoff re-check;
2. execute-plans PR #171 or #173 merges or changes state materially;
3. the parent task brief changes scope;
4. the relevant Trading Room workspace files receive a new implementation PR.

This packet therefore does not redo the full route inventory from the original
handoff. It performs the narrow re-check needed to decide whether follow-up 4's
stop-churn recommendation still holds.

Conclusion: it still holds. No material state changed.

---

## 2. Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates ownership and lifecycle; support packets do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/ag_dynui_prod_005_sidecar_bff_handoff_followup_5.md` | Task is support-only, owner `Codex2`, reviewer `Claude`; artifact target is this packet. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support docs require explicit scoped commits; unrelated dirty files are blockers. |
| `.orchestrator/skills/task-closeout-finalization.md` | Owner closeout later requires task commit, PR/merge, then `scripts/ai-status.sh done` after reviewer approval. |
| `AI_NAME=Codex2 python3 scripts/ai_status.py show AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` | Active sidecar is `in_progress`, owner `Codex2`, reviewer `Claude`, depends on archived `AG-DYNUI-PROD-004` done. |
| `AI_NAME=Codex2 python3 scripts/ai_status.py show AG-DYNUI-PROD-005` | Parent remains `todo`, owner `Claude`, reviewer `Codex2`, `last_update: 2026-07-04T00:09:32Z`; scope and acceptance remain unchanged. |
| `AI_NAME=Codex2 python3 scripts/ai_status.py show AG-DYNUI-PROD-002` | Still `review_approved`; owner closeout remains gated by hosted screenshot evidence and execute-plans PR governance. |
| `AI_NAME=Codex2 python3 scripts/ai_status.py show AG-DYNUI-PROD-003` | Still `review_approved`; hosted screenshot evidence and execute-plans PR governance remain pending. |
| `AI_NAME=Codex2 python3 scripts/ai_status.py show AG-DYNUI-PROD-004` | Archived `done`; dependency remains complete. |
| Prior packets in `support/sidecars/AG-DYNUI-PROD-005/` | Original packet and follow-ups 2/3/4 already contain the full route inventory, UI gap matrix, workshop handoff scoping question, dependency-gate characterization, and stop-churn recommendation. |
| Focused greps against `execute-plans/src` and `services/control-plane` | No drift in key implementation surface since follow-up 4; see §3. |
| GitHub checks via `gh pr view/list` and `git ls-remote` | No parent `task/AG-DYNUI-PROD-005` PR/branch exists in Pantheon or execute-plans; dependency PRs #171/#173 remain open and clean but unreviewed. |

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned, per the task-scoped read-order instruction.

---

## 3. Re-verification: Follow-up 4 Still Holds

| Check | Current result |
|---|---|
| Parent task state | `AG-DYNUI-PROD-005` remains `todo` with the same `last_update` (`2026-07-04T00:09:32Z`) reported by follow-ups 2, 3, and 4. |
| Parent branch/PR | `git ls-remote --heads origin 'task/AG-DYNUI-PROD-005'` returns no branch. `gh pr list --repo ajoe734/pantheon --head task/AG-DYNUI-PROD-005 --state all` and the same command against `ajoe734/execute-plans` both return `[]`. |
| Dependency PR #171 | `ajoe734/execute-plans` PR #171 remains `OPEN`, `MERGEABLE`, `CLEAN`, with `reviews: []`, `autoMergeRequest: null`, and a successful `integration-gate` completed at `2026-07-04T01:16:11Z`. |
| Dependency PR #173 | `ajoe734/execute-plans` PR #173 remains `OPEN`, `MERGEABLE`, `CLEAN`, with `reviews: []`, `autoMergeRequest: null`, and a successful `integration-gate` completed at `2026-07-04T03:28:21Z`. |
| Component mounting | `DashboardProposalPreview`, `WidgetRevisionDrawer`, and `DashboardChangeLog` still appear only in their own files or imports; none is mounted in the app flow. |
| Grid persistence | `TradingRoomPage.tsx` still wires `DashboardGridEditor` with `onWidgetRemove={() => {}}`, `onWidgetAdd={() => {}}`, and `onWidgetChartChange={() => {}}`; `onPlacementsChange` is still the only non-empty callback and remains local-state-only. |
| V11 workspace clients | Grep for `trading-room/workspaces`, `trading-room/proposals`, `widget-revision-proposals`, `getTradingRoomWorkspace`, and `workspaceId` outside tests/types returns no frontend client implementation. |
| Workshop to Trading Room handoff | `agora-main.tsx` still mounts `<StrategyWorkshopPage workshopId={workshopId} />` without `onAddToTradingRoom`, while `TradingRoomPage` still receives `onOpenWorkshop`. The scoping question from follow-up 2 remains undecided by the parent brief. |
| Widget allowlist/blocklist | Registry still has 42 entries. Backend `_FORBIDDEN_INTERACTIONS` and frontend `BLOCKED_INTERACTION_KINDS` are still present. |
| SSE stream | `GET /bff/agora/trading-room/stream` remains a self-documented stub and is still referenced only by route listing coverage, not by a real streaming implementation. |

No runtime, frontend, BFF, registry, governance, canonical, or contract file
was changed by this sidecar.

---

## 4. Reviewer Guidance

This follow-up adds no new implementation discovery beyond follow-up 4. Its
useful contribution is to confirm that follow-up 4's stop-churn recommendation
is still valid after the supervisor produced another sidecar dispatch.

Recommended reviewer disposition:

1. approve this packet if the narrow re-checks above are accurate;
2. tell the supervisor/parent lane not to create another identical
   `AG-DYNUI-PROD-005` BFF handoff sidecar unless one of the four trigger
   conditions in §1 changes;
3. route parent attention to the existing reader's guide instead of asking for
   another inventory:
   - original packet: full BFF route inventory, frontend gap matrix, operator journeys, and suggested client methods;
   - follow-up 2: Workshop -> Trading Room `onAddToTradingRoom` gap and scoping ambiguity;
   - follow-up 3: dependency chain blocked on two independent human gates;
   - follow-up 4: approved no-drift closeout and first stop-churn recommendation;
   - this packet: confirms no-drift and stop-churn still hold.

Recommended reviewer approval command:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/AG-DYNUI-PROD-005/AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md \
  REVIEW_NOTES_ZH="Support-only follow-up 5 核准：重新驗證 FOLLOWUP-4 的 no-drift 與 stop-churn 建議仍成立；parent AG-DYNUI-PROD-005 仍為 todo 且無 parent branch/PR，execute-plans PR #171/#173 仍 open/clean/mergeable/零 reviews，關鍵 frontend/BFF surface（未掛載元件、no-op grid callbacks、缺 V11 workspace clients、onAddToTradingRoom 缺口、allowlist/blocklist、SSE stub）未漂移。未修改 canonical truth 或 runtime 檔案；建議停止再產生同質 BFF handoff sidecar，除非 parent scope、依賴 PR 或相關 implementation surface 有實質變化。" \
  ./scripts/ai-status.sh approve AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-5 \
  "Support-only AG-DYNUI-PROD-005 follow-up 5 approved; no further identical polling sidecars recommended."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-5 \
  "Describe the factual correction, ownership-boundary issue, or missing handoff detail needed before approval."
```

---

## 5. Validation Run

Commands run from this sidecar worktree:

```bash
git status -sb
git branch --show-current
git remote -v
git fetch origin dev

AI_NAME=Codex2 python3 scripts/ai_status.py show AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-5
AI_NAME=Codex2 python3 scripts/ai_status.py show AG-DYNUI-PROD-005
AI_NAME=Codex2 python3 scripts/ai_status.py show AG-DYNUI-PROD-002
AI_NAME=Codex2 python3 scripts/ai_status.py show AG-DYNUI-PROD-003
AI_NAME=Codex2 python3 scripts/ai_status.py show AG-DYNUI-PROD-004

gh pr view 171 --repo ajoe734/execute-plans --json number,state,mergeable,mergeStateStatus,reviews,headRefName,url,statusCheckRollup,autoMergeRequest
gh pr view 173 --repo ajoe734/execute-plans --json number,state,mergeable,mergeStateStatus,reviews,headRefName,url,statusCheckRollup,autoMergeRequest
gh pr list --repo ajoe734/pantheon --head task/AG-DYNUI-PROD-005 --state all --json number,title,state,headRefName,url
gh pr list --repo ajoe734/execute-plans --head task/AG-DYNUI-PROD-005 --state all --json number,title,state,headRefName,url
git ls-remote --heads origin 'task/AG-DYNUI-PROD-005'

rg -n "DashboardProposalPreview|WidgetRevisionDrawer|DashboardChangeLog" execute-plans/src -g '*.tsx' -g '!*.test.*'
rg -n "onWidgetAdd|onWidgetRemove|onWidgetChartChange|onPlacementsChange" execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx
rg -n "trading-room/workspaces|trading-room/proposals|widget-revision-proposals|getTradingRoomWorkspace|workspaceId" execute-plans/src -g '*.ts' -g '*.tsx' -g '!*.test.*' -g '!types.ts'
rg -n "onAddToTradingRoom|onOpenWorkshop" execute-plans/src -g '*.tsx'
sed -n '75,100p' execute-plans/src/entries/agora-main.tsx
jq '[keys, (.entries | length)]' services/control-plane/specs/agora/widget_registry.v1.json
rg -n "_FORBIDDEN_INTERACTIONS|BLOCKED_INTERACTION_KINDS" services/control-plane/bff/agora execute-plans/src/agora
rg -n "trading-room/stream" services/control-plane/bff/agora
```

Expected non-zero grep behavior: the V11 workspace-client grep returns no
matches after excluding tests and `types.ts`; that is the expected result
confirming the client implementation gap remains.
