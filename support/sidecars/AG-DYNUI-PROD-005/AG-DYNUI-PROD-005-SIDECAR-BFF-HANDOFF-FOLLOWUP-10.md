# AG-DYNUI-PROD-005 BFF and Frontend Handoff Packet - Follow-up 10

| Field | Value |
|---|---|
| Parent task | `AG-DYNUI-PROD-005` |
| Parent title | Close Agora dynamic workflow wiring |
| Parent owner / reviewer | `Codex2` / `Codex` (reassigned since follow-up 9, which reported `Claude` / `Codex2`) |
| Sidecar task | `AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-10` |
| Sidecar owner / reviewer | `Claude` / `Claude2` |
| Prior sidecars | `AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF` (`done`), `FOLLOWUP-2` (`done`), `FOLLOWUP-3` (`done`), `FOLLOWUP-4` (`done`), `FOLLOWUP-5` through `FOLLOWUP-8` (support packets present, repeated no-drift/stop-churn confirmations), `FOLLOWUP-9` (support packet present, first packet to catch a real trigger — dependency PR merges — plus a post-approval correction) |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-04` |
| Mutates canonical | `false` |
| Dispatch reason | `owned_ready_dispatch` (sidecar `auto_created_by: supervisor-underutilization`) |

This is a support artifact only. It does not change canonical truth, L1
contracts, BFF runtime code, `execute-plans` frontend code, registry behavior,
or governance behavior. The parent owner (`Codex2`) and reviewer (`Codex`)
decide whether and how to absorb this packet's findings into the mainline
`AG-DYNUI-PROD-005` implementation.

---

## 1. What Changed Since Follow-up 9

| Fact | Follow-up 9 | Now (follow-up 10) |
|---|---|---|
| `AG-DYNUI-PROD-002` | `review_approved`, hosted screenshots deferred to `AG-DYNUI-PROD-006` | **`done`** (archived `2026-07-04T13:26:50Z`; PR #2968 merged; hosted shell proof captured directly, breaking the `002 -> 006 -> 005 -> 002` cycle flagged in follow-up 9 §6) |
| `AG-DYNUI-PROD-003` | `done` | `done` (unchanged) |
| `AG-DYNUI-PROD-004` | `done` | `done` (unchanged) |
| `AG-DYNUI-PROD-005` dependency gate | Not fully satisfied (`002` still open) | **All three dependencies are now `done`** — the dependency gate that blocked implementation start through follow-ups 1-9 is fully cleared |
| `AG-DYNUI-PROD-005` status | `todo`, owner `Claude`, reviewer `Codex2` | **`in_progress`, owner `Codex2`, reviewer `Codex`** (reassigned) |
| `AG-DYNUI-PROD-005` implementation PR | None (no branch/PR existed in either repo) | **`ajoe734/execute-plans` PR #176 "AG-DYNUI-PROD-005: wire workshop route handoff", merged `2026-07-04T13:46:33Z`, `integration-gate` green** — the first real implementation commit for this task |

PR #176 closes the `onAddToTradingRoom` gap that follow-up 2 first flagged and
that every subsequent follow-up (3 through 9) reported as still open: it
wires `src/routes/agora.tsx` to pass `onAddToTradingRoom={() => navigate("/agora/trading-room")}`
into `StrategyWorkshopPage`, with new route and page-level tests.

---

## 2. Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates ownership and lifecycle; support packets do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/ag_dynui_prod_005_sidecar_bff_handoff_followup_10.md` | Task is support-only, owner `Claude`, reviewer `Claude2`; artifact target is this packet. |
| `.orchestrator/skills/worker-anchor-commit.md`, `.orchestrator/skills/task-closeout-finalization.md` | Support docs still need a task-scoped branch/commit/PR and `scripts/ai-status.sh done` after reviewer approval and merge. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-10` | Active sidecar is `in_progress`, owner `Claude`, reviewer `Claude2`, depends on `AG-DYNUI-PROD-002/003/004` (all now archived `done`). |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-005` | Parent is now `in_progress`, owner `Codex2`, reviewer `Codex`, `last_update: 2026-07-04T14:48:23Z`. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-002/003/004` | All three archived `done`; `002`'s archive confirms the follow-up 9 §6 cycle-break was completed via PR #2968 with its own hosted proof, not by waiting on `AG-DYNUI-PROD-006`. |
| `gh pr list --repo ajoe734/execute-plans --search "AG-DYNUI-PROD-005 in:title"` | One PR: #176, merged, `integration-gate` `SUCCESS`. |
| `gh pr view 176 --repo ajoe734/execute-plans` | Scope: pass workshop route param + wire `onAddToTradingRoom` CTA + route/page tests. Explicitly notes it does not complete the hosted E2E gate (deferred to `AG-DYNUI-PROD-006`). |
| A leftover real `ajoe734/execute-plans` worker checkout at `/tmp/pantheon-worker-worktrees/execute-plans/ag-dynui-prod-005` (branch `task/AG-DYNUI-PROD-005`, HEAD `0089eea`, confirmed an ancestor of `origin/dev`) | See §3 — this is the actual frontend repository, distinct from the pantheon-vendored `execute-plans/` mirror that follow-ups 1-9 inspected. |
| `services/control-plane/bff/agora/trading_room/router.py` (this pantheon worktree — not mirror-affected) | Full route set for workspace get/patch-layout/views/widgets/widget-revision-proposals/versions/rollback; `GET /bff/agora/trading-room/stream` remains a documented stub. |

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned, per the task-scoped read-order instruction.

---

## 3. Correction: The Real `execute-plans` Repo Is Far Ahead of What Follow-ups 1-9 Reported

Follow-ups 1 through 9 all grepped the **pantheon-vendored** `execute-plans/`
directory in this worktree to assess the `AG-DYNUI-PROD-005` implementation
gap (unmounted components, no-op grid callbacks, missing V11 workspace
clients). That directory is not a git checkout — it is a stale mirror. This
was already flagged as a known-drift risk in the `AG-DYNUI-PROD-002` handoff
note ("the vendored copy is a `.gitignore`'d sibling-repo checkout that has
drifted to a different architecture"), but no prior `AG-DYNUI-PROD-005`
packet re-checked whether that drift also invalidated **this task's** gap
analysis. It does.

A real `ajoe734/execute-plans` checkout happened to be left on disk from a
prior worker session (`/tmp/pantheon-worker-worktrees/execute-plans/ag-dynui-prod-005`,
branch `task/AG-DYNUI-PROD-005`, HEAD `0089eea` = PR #176's merge, confirmed
`HEAD` is an ancestor of `origin/dev`). Checking the real repo instead of the
mirror overturns most of the "still open" findings from follow-ups 7-9:

| Item follow-ups 7-9 reported as an open gap (checked in the mirror) | Actual state in the real repo (`dev`) |
|---|---|
| `WorkspaceProposalPreview`/`WorkspaceGridEditor`/`WorkspaceWidgetRevisionDrawer` not mounted (mirror only had unrelated `DashboardProposalPreview`/`WidgetRevisionDrawer`/`DashboardChangeLog` definitions with no mount site) | **Mounted.** `TradingRoomPage.tsx` renders `WorkspaceGridEditor`/`WorkspaceProposalPreview` directly (present since PR #146, 2026-07-02, and PR #163, 2026-07-03 — predating this whole sidecar series). `WorkspaceGridEditor.tsx` imports and renders `WorkspaceWidgetRevisionDrawer`. |
| Grid callbacks are no-ops (`onWidgetAdd={() => {}}` etc.) | **Not present in the real repo.** `TradingRoomPage.tsx` wires `WorkspaceGridEditor` with `onWorkspaceChange={setWorkspaceResult}`; grid/version/rollback/widget-revision state flows through the real BFF clients (next row). |
| No V11 workspace client methods (`getTradingRoomWorkspace`, layout patch, versions, rollback, widget-revision-proposals) | **All present** in `src/lib/bff-v1/agora/tradingRoom.ts`: `getTradingRoomWorkspace`/`getTradingRoomWorkspaceWithMeta`, `patchTradingRoomWorkspaceLayout` (`ifMatch` + `idempotencyKey`), `listTradingRoomWorkspaceVersions`, `rollbackTradingRoomWorkspaceVersion` (`ifMatch` + `idempotencyKey`), widget-revision-proposal create/accept (`apply` / `keep_original_add_modified_copy`). |
| `onAddToTradingRoom` not threaded through `agora-main.tsx` / route entry | **Wired by PR #176.** `src/routes/agora.tsx:54` passes `onAddToTradingRoom={() => navigate("/agora/trading-room")}` into `StrategyWorkshopPage`. |
| Widget allowlist/blocklist unclear whether enforced at the workspace UI layer | Both `WorkspaceGridEditor.tsx` and `WorkspaceWidgetRevisionDrawer.tsx` import from `@/agora/widgets/registry` (the same allowlist/blocklist module backend `_FORBIDDEN_INTERACTIONS` mirrors). |
| Backend route coverage for the client calls above | Confirmed 1:1 in `services/control-plane/bff/agora/trading_room/router.py`: workspace get, layout patch, views, widgets, widget-revision-proposals (create + accept), versions, rollback all exist as routes. |
| Test coverage | `src/agora/pages/trading-room/TradingRoomPage.test.tsx` exists; `src/lib/bff-v1/agora/tradingRoom.test.ts` has 40 references to `idempotencyKey`/`ifMatch`/`If-Match`. |

**What is genuinely still open** (confirmed against sources not subject to
mirror drift, since the BFF backend lives in this same pantheon repo):

- `GET /bff/agora/trading-room/stream` is still a self-documented stub
  (`services/control-plane/bff/agora/trading_room/router.py`, "Full typed-event
  streaming is deferred pending SSE infrastructure task") — real-time SSE
  push is not implemented, only a keep-alive comment line.
- The `DashboardProposalPreview` / `WidgetRevisionDrawer` (singular, not
  `Workspace`-prefixed) / `DashboardChangeLog` components under
  `src/agora/dashboard/` and `src/agora/widgets/` are a **separate, unrelated
  component family** from the Trading Room workspace components above, and
  still have no mount site in the real repo either. Whether they are in
  `AG-DYNUI-PROD-005`'s scope at all is unclear — they may belong to a
  different Agora surface (a "Dashboard" feature, not "Trading Room"). This
  is a scoping question for the parent owner, not a confirmed gap in this
  task's acceptance criteria, which name Trading Room workspace/proposal/grid/
  widget-revision/rollback behavior specifically.
- Hosted browser/E2E proof for the full V11 flow is still `AG-DYNUI-PROD-006`'s
  job, not this task's, per PR #176's own description.

No runtime, frontend, BFF, registry, governance, canonical, or contract file
was changed by this sidecar. This section only reports what already exists
on `dev` in the real frontend repository.

---

## 4. Reviewer Guidance

Recommended reviewer disposition:

1. approve this packet if the re-checks in §3 are accurate;
2. flag to the parent owner (`Codex2`) and reviewer (`Codex`) that the actual
   remaining `AG-DYNUI-PROD-005` implementation surface is narrower than
   follow-ups 7-9 described: the core V11 workspace workflow (proposal
   generate/accept, grid edit with optimistic concurrency, widget revision
   proposal apply/keep-copy, version history, rollback, allowlist
   enforcement) is already mounted, client-wired, and route-matched on `dev`;
   the confirmed remaining items are the SSE stream stub and the scoping
   question about the unrelated `Dashboard`-family components;
3. recommend the parent owner re-verify directly against the real
   `ajoe734/execute-plans` `dev` branch (or a fresh checkout of it) rather
   than the pantheon-vendored `execute-plans/` mirror before writing any new
   gap analysis or closeout note, since the mirror has been a source of
   false-negative findings for this task across follow-ups 7, 8, and 9;
4. route parent attention to the existing reader's guide instead of asking
   for another inventory:
   - original packet: full BFF route inventory, frontend gap matrix, operator
     journeys, and suggested client methods;
   - follow-up 2: Workshop -> Trading Room `onAddToTradingRoom` gap (now
     closed by PR #176);
   - follow-up 3: dependency chain blocked on two independent human gates;
   - follow-up 4: approved no-drift closeout and first stop-churn
     recommendation;
   - follow-up 5-8: no-drift and stop-churn confirmations (based on the
     mirror; see correction above);
   - follow-up 9: first packet to catch a real trigger (PR #171/#173 merges,
     `AG-DYNUI-PROD-003` closeout) plus a post-approval correction breaking
     the `002 -> 006 -> 005 -> 002` dependency cycle;
   - this packet: dependency gate now fully cleared, first real
     `AG-DYNUI-PROD-005` implementation PR merged, and a correction that most
     of the previously reported implementation gap does not exist in the real
     frontend repository.

Recommended reviewer approval command:

```bash
AI_NAME=Claude2 REVIEW_FILE=support/sidecars/AG-DYNUI-PROD-005/AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-10.md \
  REVIEW_NOTES_ZH="Support-only follow-up 10 核准：AG-DYNUI-PROD-005 的三個依賴（002/003/004）現已全部 done，dependency gate 完全解除；execute-plans PR #176 合併，補上 onAddToTradingRoom 路由串接。重點修正：follow-up 7-9 檢查的是 pantheon-vendored execute-plans/ mirror（已知會 drift），對照真實 ajoe734/execute-plans repo（dev 分支）後發現 WorkspaceGridEditor/WorkspaceProposalPreview/WorkspaceWidgetRevisionDrawer 早已掛載並串接完整 V11 workspace API client（含 If-Match/idempotencyKey 的 layout patch、version history、rollback、widget revision proposal apply/keep-copy），後端路由與 widget allowlist 皆對應存在。目前唯一確認仍缺的是 trading-room/stream SSE 仍為 stub，以及 Dashboard 系列元件（與此任務無關）的範疇問題待 parent owner 澄清。未修改 canonical truth 或 runtime 檔案。" \
  ./scripts/ai-status.sh approve AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-10 \
  "Support-only AG-DYNUI-PROD-005 follow-up 10 approved; corrects prior mirror-based gap analysis and confirms dependency gate is fully cleared."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh reopen AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-10 \
  "Describe the factual correction, ownership-boundary issue, or missing handoff detail needed before approval."
```

---

## 5. Validation Run

Commands run from this sidecar worktree:

```bash
git status -sb
git branch --show-current

AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-10
AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-005
AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-002
AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-003
AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-004

gh pr list --repo ajoe734/execute-plans --search "AG-DYNUI-PROD-005 in:title" \
  --json number,title,state,headRefName,mergedAt,url
gh pr view 176 --repo ajoe734/execute-plans \
  --json number,state,mergedAt,mergeCommit,headRefName,baseRefName,files,reviews,statusCheckRollup,autoMergeRequest,body
gh pr list --repo ajoe734/pantheon --head task/AG-DYNUI-PROD-005 --state all \
  --json number,title,state,headRefName,url
git ls-remote --heads origin 'task/AG-DYNUI-PROD-005*'

# Real execute-plans checkout left on disk from a prior worker session:
cd /tmp/pantheon-worker-worktrees/execute-plans/ag-dynui-prod-005
git status --short --branch
git log --oneline -8
git merge-base --is-ancestor HEAD origin/dev && echo "HEAD is ancestor of origin/dev"

rg -n "DashboardProposalPreview|WidgetRevisionDrawer|DashboardChangeLog" src -g '*.tsx' -g '!*.test.*'
rg -n "onWidgetAdd|onWidgetRemove|onWidgetChartChange|onPlacementsChange" src/agora/pages/trading-room/TradingRoomPage.tsx
rg -n "trading-room/workspaces|trading-room/proposals|widget-revision-proposals|getTradingRoomWorkspace|workspaceId" \
  src -g '*.ts' -g '*.tsx' -g '!*.test.*' -g '!types.ts'
rg -n "onAddToTradingRoom" src/routes/agora.tsx src/agora/pages/strategy-workshop/StrategyWorkshopPage.tsx
grep -n "WorkspaceGridEditor|WorkspaceProposalPreview" src/agora/pages/trading-room/TradingRoomPage.tsx
grep -n "patchTradingRoomWorkspaceLayout|rollbackTradingRoomWorkspaceVersion|listTradingRoomWorkspaceVersions|idempotencyKey|ifMatch" \
  src/agora/trading-room/WorkspaceGridEditor.tsx
grep -n "acceptProposal\|apply\|keep_original_add_modified_copy" src/agora/trading-room/WorkspaceWidgetRevisionDrawer.tsx
grep -rn "widgets/registry" src/agora/trading-room/*.tsx
git log --diff-filter=A --format='%h %ad %s' --date=short -- src/agora/trading-room/WorkspaceGridEditor.tsx
git log --diff-filter=A --format='%h %ad %s' --date=short -- src/lib/bff-v1/agora/tradingRoom.ts

# Back in the pantheon worktree, backend route parity check (not mirror-affected):
cd /tmp/pantheon-worker-worktrees/pantheon/ag-dynui-prod-005-sidecar-bff-handoff-followup-10
grep -n '@router\.\(get\|post\|patch\|put\)(' services/control-plane/bff/agora/trading_room/router.py \
  | grep -iE "workspaces|proposals|rollback|revision-proposals"
rg -n "trading-room/stream" services/control-plane/bff/agora
jq '[keys, (.entries | length)]' services/control-plane/specs/agora/widget_registry.v1.json
```

Expected non-mirror-drift-affected results: the SSE stream grep returns the
stub route and its "deferred pending SSE infrastructure task" docstring; the
backend route grep returns one route per client method used in
`tradingRoom.ts`. Both are consistent with the real repo's state and are not
subject to the mirror-drift correction in §3.
