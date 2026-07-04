# AG-DYNUI-PROD-005 BFF and Frontend Handoff Packet - Follow-up 2

| Field | Value |
|---|---|
| Parent task | `AG-DYNUI-PROD-005` |
| Parent title | Close Agora dynamic workflow wiring |
| Parent owner / reviewer | `Claude` / `Codex2` |
| Sidecar task | `AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Sidecar owner / reviewer | `Claude2` / `Claude` |
| Prior sidecar | `AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF` (`done`, PR #2866 + #2870, packet at `support/sidecars/AG-DYNUI-PROD-005/AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF.md`) |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-04` |
| Mutates canonical | `false` |

This is a support artifact only. It does not change canonical truth, L1
contracts, BFF runtime code, `execute-plans` frontend code, registry behavior,
or governance behavior. The parent owner (`Claude`) and reviewer (`Codex2`)
decide whether and how to absorb this packet into the mainline closeout.

---

## 1. Why This Follow-up Exists

The original sidecar (`AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF`) closed `done`
while the parent (`AG-DYNUI-PROD-005`) was still `todo` with an unstarted
dependency chain. This follow-up was auto-dispatched
(`auto_created_by: supervisor-underutilization`) after ownership of this
sidecar lane was reassigned from `Codex2` to `Claude2` (Codex usage limit
reached; task returned to `todo` until a fresh run started). Its job is to
re-verify whether anything material changed since the original packet closed,
not to redo the full inventory.

## 2. Sources Read

| Source | Relevant finding |
|---|---|
| `.orchestrator/task-briefs/ag_dynui_prod_005_sidecar_bff_handoff_followup_2.md` | Reassignment note: Codex2 hit a usage-limit terminal; task returned to `todo` until `Claude2` started this fresh run. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | Sidecar is `in_progress`, owner `Claude2`, reviewer `Claude`, `depends_on: AG-DYNUI-PROD-004` (done). |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-005` | Parent still `status: todo`, owner `Claude`, reviewer `Codex2`, `last_update: 2026-07-04T00:09:32Z`; scope/acceptance unchanged from the original packet's read. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-002` | `review_approved` as of `2026-07-04T02:01:00Z` (very recent). Reviewer note defers hosted desktop/mobile screenshots to `AG-DYNUI-PROD-006`; owner must not run `done` before that evidence exists. Scope is shell architecture (`App.tsx`, `PlatformShell.tsx`, `TradingDeskLayout.tsx`, `routes/agora.tsx`) — orthogonal to PROD-005's dynamic-workflow scope. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-003` | `review_approved`, PR #2860 merged (`ec5d902fc`/`eab6e0cfd`), reviewer note: hosted screenshot evidence still owed before owner finalizes to `done`. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-004` | Archived `done`; PR #2855 merged, nonprod deploy run `28689452900` succeeded, hosted probe passed. |
| `git log --oneline -15 -- execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx execute-plans/src/agora/dashboard/ execute-plans/src/agora/widgets/WidgetRevisionDrawer.tsx execute-plans/src/lib/bff-v1/agora/tradingRoom.ts execute-plans/src/lib/bff-v1/agora/dashboard.ts execute-plans/src/entries/agora-main.tsx execute-plans/src/routes/agora.tsx services/control-plane/bff/agora/trading_room/` | Most recent touches to these paths since the original packet are `eab6e0cfd` (PROD-003 default-entry) and `23a537ab7`/`d6065dec6` (PROD-004 diagnostics). Neither adds V11 workspace client functions, mounts the three unmounted components, or replaces the grid editor's no-op callbacks. |
| Direct `grep` re-runs (component mounting, no-op callbacks, V11 client functions) against the current worktree | All three findings from the original packet reproduce identically — see §3. |
| `grep -rn "onAddToTradingRoom\|onOpenWorkshop" execute-plans/src` plus a read of `execute-plans/src/entries/agora-main.tsx` (lines 1-120) and `StrategyWorkshopPage.tsx` (props/mount site) | New finding this follow-up adds — see §4. |
| `python3 -c "... widget_registry.v1.json ..."`, `grep -n "_FORBIDDEN_INTERACTIONS\|BLOCKED_INTERACTION_KINDS"`, `grep -n "trading-room/stream"` | Widget allowlist (42 entries), shared forbidden-interaction blocklist, and the SSE-stream stub are all unchanged from the original packet's description. |
| `git log --all --oneline \| grep -i "AG-DYNUI-PROD-005"` and `gh pr list --repo ajoe734/pantheon --search "AG-DYNUI-PROD-005"` | Only the original sidecar's two PRs (#2866, #2870) exist. No branch, commit, or PR for the parent task itself has ever been created — implementation has not started. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned, per
the task-scoped read-order instruction.

---

## 3. Re-verification: Original Packet's Core Findings Are Unchanged

| Check | Original packet claim | Re-run result (`2026-07-04`, this follow-up) |
|---|---|---|
| Artifact path correction | Named `WorkspaceProposalPreview.tsx` / `WorkspaceGridEditor.tsx` / `WorkspaceWidgetRevisionDrawer.tsx` / flat `trading_room.py` do not exist; real files are `DashboardProposalPreview.tsx` / `DashboardGridEditor.tsx` / `WidgetRevisionDrawer.tsx` / `trading_room/router.py`+`store.py`. | `find execute-plans/src/agora -maxdepth 3 -type d` and `ls` confirm the same real paths; the named paths still do not exist. |
| Component mounting | `DashboardGridEditor` mounted with 3 no-op callbacks + 1 local-state-only callback; `DashboardProposalPreview`, `WidgetRevisionDrawer`, `DashboardChangeLog` never mounted anywhere. | `grep -rn "DashboardProposalPreview\|WidgetRevisionDrawer\|DashboardChangeLog" execute-plans/src --include="*.tsx" \| grep -v "\.test\."` still returns only each component's own file. `grep -n "onWidgetAdd\|onWidgetRemove\|onWidgetChartChange\|onPlacementsChange" TradingRoomPage.tsx` still shows the same 3 no-ops (`() => {}`) and the same local-state-only `onPlacementsChange`. |
| V11 client functions | Zero client functions exist for any of the 15 in-scope workspace/proposal/layout/widget-revision/version routes. | `grep -rn "trading-room/workspaces\|trading-room/proposals\|widget-revision-proposals\|getTradingRoomWorkspace\|workspaceId" execute-plans/src --include="*.ts" --include="*.tsx" \| grep -v "\.test\." \| grep -v types.ts` still returns zero matches. |
| Widget allowlist / blocklist | 42-entry `widget_registry.v1.json`; shared `_FORBIDDEN_INTERACTIONS` (backend) / `BLOCKED_INTERACTION_KINDS` (frontend). | Registry still reports 42 entries; both blocklists still present in `dashboard/router.py`, `trading_room/router.py`, and `registry.ts`. |
| SSE stream stub | `GET /bff/agora/trading-room/stream` is a self-documented stub. | Route and its stub comment (`# GET /bff/agora/trading-room/stream`) still present at `trading_room/router.py:2811-2814`. |

**Conclusion:** none of the original packet's factual claims have drifted.
`AG-DYNUI-PROD-005` implementation has not started — no branch, commit, or PR
exists for the parent task itself (only this sidecar's own two merged PRs).
The dependency chain has progressed (`PROD-002`/`PROD-003` now
`review_approved`, `PROD-004` `done`), but none of those changes touched the
files this task must wire.

---

## 4. New Finding: Workshop -> Trading Room Handoff Is Confirmed Unwired, And Its Ownership Is Ambiguous

The two prior `AG-DYNUI-PROD-003` sidecar follow-ups (`FOLLOWUP-2`, `FOLLOWUP-3`)
both name the `onAddToTradingRoom` wiring gap as "candidate: `AG-DYNUI-PROD-005`"
without confirming it against this task's own brief. This follow-up checked
both sides directly.

### 4.1 The gap is real and reproducible in this worktree

- `StrategyWorkshopPage` declares an optional `onAddToTradingRoom?: () => void`
  prop (`StrategyWorkshopPage.tsx:194,524,527`) and only exercises it in its
  own test file — never in application code.
- `execute-plans/src/entries/agora-main.tsx:87` mounts
  `<StrategyWorkshopPage workshopId={workshopId} />` with **no**
  `onAddToTradingRoom` prop supplied. The Trading Room side already receives
  an equivalent `onOpenWorkshop` callback (line 93,
  `() => handleTabChange("strategy-workshop")`), so the entry point wires one
  direction (Trading Room -> Workshop) but not the other (Workshop -> Trading
  Room).
- Net effect: a servant/operator inside the Strategy Workshop who reaches a
  ready-to-trade recipe has no button that lands them back in the Trading
  Room for that strategy — confirmed by direct mount-site inspection, not
  inferred from the component's prop signature alone.

### 4.2 Whether this belongs to `AG-DYNUI-PROD-005` is not settled by the task brief

`docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-005-dynamic-workflow-closeout.md`
scopes this task strictly to: proposal generation, proposal acceptance,
workspace load, layout patch, widget revision proposal, apply/keep-copy,
version history, and rollback — all *within* an already-open Trading Room.
Entering the Trading Room from the Workshop is a distinct navigation concern,
not one of the eight listed operations, and the brief does not mention
`agora-main.tsx`, `StrategyWorkshopPage.tsx`, or `onAddToTradingRoom`
anywhere.

**Recommendation for parent owner/reviewer:** do not silently fold this
wiring into `AG-DYNUI-PROD-005`'s closeout on the strength of the `PROD-003`
sidecar's phrasing alone. Either (a) explicitly confirm it in scope here and
wire `onAddToTradingRoom={() => handleTabChange("trading-room")}` (plus
whatever strategy-id/workspace-id handoff state that requires) as part of
this task's PR, or (b) file it as its own follow-up task and note the
decision in `AG-DYNUI-PROD-005`'s closeout record so it is not lost between
two tasks that each assumed the other owned it.

---

## 5. Parent Scope Boundary (unchanged from the original packet)

`AG-DYNUI-PROD-005` still owns:

- Adding the missing frontend BFF client functions for the 9 V11
  workspace/proposal/layout/widget-revision/version routes (none exist).
- Mounting `DashboardProposalPreview`, `WidgetRevisionDrawer`, and
  `DashboardChangeLog` into the real Trading Room page flow, and replacing
  `DashboardGridEditor`'s no-op/local-only callbacks with real BFF-backed
  handlers.
- Testing idempotency, optimistic concurrency (ETag/If-Match), scope
  isolation, and widget allowlist enforcement for every one of these
  operations.
- Deciding whether the legacy `dashboard-recipes` surface (`dashboard.ts`)
  is retired, kept parallel, or migrated onto the new
  `trading-room/workspaces/{workspace_id}` surface.

`AG-DYNUI-PROD-005` still does **not** own:

- The decision-event queue workflow (already implemented, tested, out of
  scope).
- Any order-routing, `RuntimeBinding`, or capital-binding interaction (both
  blocklists already enforce this and must not be weakened).
- The SSE stream stub (self-documented as deferred).
- `AG-DYNUI-PROD-002` (shell architecture, `review_approved`, hosted
  screenshots deferred to `PROD-006`) and `AG-DYNUI-PROD-003` (default entry,
  `review_approved`, hosted screenshots still owed) — both are upstream
  dependencies, not in-scope work.
- Whether the Workshop -> Trading Room `onAddToTradingRoom` handoff is
  in-scope is **not yet decided** — see §4.2. Treat it as an open scoping
  question, not a settled inclusion.

---

## 6. Reviewer Handoff

Reviewer (`Claude`) should verify:

1. This packet is support-only and made no change to canonical truth, BFF
   runtime, registry/governance code, or `execute-plans` frontend code.
2. §3's re-verification is accurate: re-run the five greps/checks if the
   worktree has moved since this packet's timestamp.
3. §4's new finding is accurate: `agora-main.tsx:87` genuinely omits
   `onAddToTradingRoom` when mounting `StrategyWorkshopPage`, and the parent
   task brief genuinely does not name this handoff as in-scope.
4. §4.2's recommendation (decide explicitly, don't inherit silently from the
   `PROD-003` sidecar's phrasing) is a fair scoping note for parent owner
   `Claude` and reviewer `Codex2`, not an overreach into deciding scope on
   their behalf.
5. Parent (`AG-DYNUI-PROD-005`) is confirmed still unstarted (`todo`, no
   branch/PR), so this packet remains a useful pre-implementation handoff
   rather than a stale one.

Recommended reviewer approval command:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/AG-DYNUI-PROD-005/AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md \
  REVIEW_NOTES_ZH="Support-only follow-up 2 核准：重新驗證原始 packet 的 artifact 路徑更正、元件掛載狀態、V11 client function 缺口、widget allowlist/blocklist 與 SSE stub 現況皆未變動；新增發現 StrategyWorkshopPage 掛載處未傳入 onAddToTradingRoom，且此 handoff 是否屬於 AG-DYNUI-PROD-005 範疇尚未在 task brief 中確認，建議 owner/reviewer 明確決定歸屬而非沿用 PROD-003 sidecar 的推測。未修改 canonical truth 或 runtime 檔案。" \
  ./scripts/ai-status.sh approve AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 \
  "Support-only AG-DYNUI-PROD-005 follow-up 2 approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 \
  "Describe the factual correction, ownership-boundary issue, or missing handoff detail needed before approval."
```

---

## 7. Validation Run

Commands run from this sidecar worktree:

```bash
git branch --show-current
# task/AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-2

git status --short

AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-2
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-005
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-002
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-003
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-004

find execute-plans/src/agora -maxdepth 3 -type d
ls execute-plans/src/agora/widgets/ execute-plans/src/agora/dashboard/ execute-plans/src/agora/pages/trading-room/
git log --oneline -15 -- execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx \
  execute-plans/src/agora/dashboard/ execute-plans/src/agora/widgets/WidgetRevisionDrawer.tsx \
  execute-plans/src/lib/bff-v1/agora/tradingRoom.ts execute-plans/src/lib/bff-v1/agora/dashboard.ts \
  execute-plans/src/entries/agora-main.tsx execute-plans/src/routes/agora.tsx \
  services/control-plane/bff/agora/trading_room/

grep -rn "DashboardProposalPreview\|WidgetRevisionDrawer\|DashboardChangeLog" execute-plans/src --include="*.tsx" | grep -v "\.test\."
grep -n "onWidgetAdd\|onWidgetRemove\|onWidgetChartChange\|onPlacementsChange" execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx
grep -rn "trading-room/workspaces\|trading-room/proposals\|widget-revision-proposals\|getTradingRoomWorkspace\|workspaceId" execute-plans/src --include="*.ts" --include="*.tsx" | grep -v "\.test\." | grep -v types.ts

grep -rn "onAddToTradingRoom\|onOpenWorkshop" execute-plans/src
find execute-plans/src -iname "agora-main.tsx" -o -iname "App.tsx" -o -iname "PlatformShell.tsx" -o -iname "TradingDeskLayout.tsx"
grep -rn "StrategyWorkshopPage" execute-plans/src --include="*.tsx" | grep -v "\.test\."
sed -n '1,120p' execute-plans/src/entries/agora-main.tsx

python3 -c "import json; d=json.load(open('services/control-plane/specs/agora/widget_registry.v1.json')); print(len(d.get('widgets', d)))"
grep -n "_FORBIDDEN_INTERACTIONS\|BLOCKED_INTERACTION_KINDS" -r services/control-plane/bff/agora execute-plans/src/agora
grep -n "trading-room/stream" -r services/control-plane/bff/agora

git log --all --oneline | grep -i "AG-DYNUI-PROD-005"
gh pr list --repo ajoe734/pantheon --search "AG-DYNUI-PROD-005" --state all --json number,title,state,headRefName,url
git ls-remote --heads origin 'task/AG-DYNUI-PROD-005*'
```

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned, per the task-scoped read-order instruction. No runtime, canonical,
registry, governance, or frontend change was made by this sidecar —
verification was read-only inspection of the worktree, `ai-status.json`
snapshots, and GitHub PR/branch metadata.
