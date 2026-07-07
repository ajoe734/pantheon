# AG-DYNUI-PROD-005 BFF and Frontend Handoff Packet - Follow-up 3

| Field | Value |
|---|---|
| Parent task | `AG-DYNUI-PROD-005` |
| Parent title | Close Agora dynamic workflow wiring |
| Parent owner / reviewer | `Claude` / `Codex2` |
| Sidecar task | `AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` |
| Sidecar owner / reviewer | `Claude2` / `Claude` |
| Prior sidecars | `AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF` (`done`, PR #2866 + #2870), `AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` (`done`, PR #2876 + #2878) |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-04` |
| Mutates canonical | `false` |

This is a support artifact only. It does not change canonical truth, L1
contracts, BFF runtime code, `execute-plans` frontend code, registry behavior,
or governance behavior. The parent owner (`Claude`) and reviewer (`Codex2`)
decide whether and how to absorb this packet into the mainline closeout.

---

## 1. Why This Follow-up Exists

`AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` closed `done` while the
parent (`AG-DYNUI-PROD-005`) was still `todo` with an unstarted dependency
chain. This follow-up was auto-dispatched
(`auto_created_by: supervisor-underutilization`, reason `owned_ready_dispatch`)
after ownership of this sidecar lane was reassigned from `Copilot` to
`Claude2` (Copilot hit a monthly-quota terminal; task returned to `todo`
until a fresh run started — see
`.orchestrator/task-briefs/ag_dynui_prod_005_sidecar_bff_handoff_followup_3.md`).
Its job is to re-verify whether anything material changed since FOLLOWUP-2's
close, characterize how the dependency chain is actually blocked right now,
and give the parent owner a single reader's guide across all three packets.

## 2. Sources Read

| Source | Relevant finding |
|---|---|
| `.orchestrator/task-briefs/ag_dynui_prod_005_sidecar_bff_handoff_followup_3.md` | Reassignment note: Copilot hit a monthly quota terminal; task returned to `todo` until `Claude2` started this fresh run. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` | Sidecar is `in_progress`, owner `Claude2`, reviewer `Claude`, `depends_on: AG-DYNUI-PROD-004` (done). |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-005` | Parent still `status: todo`, owner `Claude`, reviewer `Codex2`, `last_update: 2026-07-04T00:09:32Z` — **byte-for-byte the same timestamp FOLLOWUP-2 already read**. Scope/acceptance unchanged. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-002` | Still `review_approved`, but `last_update` advanced to `2026-07-04T02:20:33Z`. `next` now records a 10th finalize-dispatch re-check: PR #171 (on `ajoe734/execute-plans`) still `OPEN`/`MERGEABLE`, zero reviews, self-merge governance-blocked pending a human; `AG-DYNUI-PROD-006` (hosted E2E gate) still `todo` so hosted screenshot proof is still missing; supervisor flagged the repeated re-check cadence for possible chair-review backoff tuning. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-003` | Still `review_approved`, PR #2860 merged. **New since FOLLOWUP-2:** owner reassigned `Codex` → `Codex2` (`next`: "Auto-reassigned ownership from Codex to Codex2 after repeated Codex terminal: Codex usage limit reached"). Review notes (hosted screenshot evidence still owed) are unchanged. |
| `gh pr view 171 --repo ajoe734/execute-plans --json number,state,mergeable,reviews,headRefName` | Confirms independently: `state=OPEN`, `mergeable=MERGEABLE`, `reviews=[]`, head `task/AG-DYNUI-PROD-002-agora-standalone-shell-compliant` — matches PROD-002's own status note exactly. |
| `gh pr list --repo ajoe734/execute-plans --search "AG-DYNUI-PROD-005" --state all` | Zero results — no PR referencing this task exists on the standalone frontend repo either. |
| `git log --all --oneline \| grep -i "AG-DYNUI-PROD-005"` (excluding sidecar branches) | Zero results — no branch or commit for the parent task itself exists anywhere in this repo's history. |
| `git log --format="%h %ad %s" --date=iso -1 <hash>` for every commit FOLLOWUP-2 cited as "most recent touch" (`eab6e0cfd`, `23a537ab7`, `d6065dec6`) plus the six commits immediately preceding them in the same path history | All predate FOLLOWUP-2's own generation timestamp; nothing has landed on any PROD-005-relevant path since FOLLOWUP-2 closed. |
| Direct `grep` re-runs (component mounting, no-op callbacks, V11 client functions, `onAddToTradingRoom`/`onOpenWorkshop`) against the current worktree | All findings from the original packet and FOLLOWUP-2 reproduce identically — see §3. |
| `python3` re-parse of `services/control-plane/specs/agora/widget_registry.v1.json` using the correct top-level key (`entries`, not a bare `widgets` fallback) | Registry still holds exactly 42 entries; see §3.1 for why this needed re-checking. |

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned, per the task-scoped read-order instruction.

---

## 3. Re-verification: No Drift In The Implementation Surface Since FOLLOWUP-2

| Check | FOLLOWUP-2 claim | Re-run result (this follow-up) |
|---|---|---|
| Artifact path correction | Named `WorkspaceProposalPreview.tsx` / `WorkspaceGridEditor.tsx` / `WorkspaceWidgetRevisionDrawer.tsx` / flat `trading_room.py` do not exist; real files are `DashboardProposalPreview.tsx` / `DashboardGridEditor.tsx` / `WidgetRevisionDrawer.tsx` / `trading_room/router.py`+`store.py`. | Unchanged — same real paths still confirmed present, same named paths still absent. |
| Component mounting | `DashboardGridEditor` mounted with 3 no-op callbacks + 1 local-state-only callback; `DashboardProposalPreview`, `WidgetRevisionDrawer`, `DashboardChangeLog` never mounted anywhere. | `grep -rn "DashboardProposalPreview\|WidgetRevisionDrawer\|DashboardChangeLog" execute-plans/src --include="*.tsx" \| grep -v "\.test\."` still returns only each component's own file (definition/export/import). `TradingRoomPage.tsx:991-993` still shows `onWidgetRemove={() => {}}`, `onWidgetAdd={() => {}}`, `onWidgetChartChange={() => {}}` verbatim; `onPlacementsChange` (line 988) remains the only wired callback. |
| V11 client functions | Zero client functions exist for any of the in-scope workspace/proposal/layout/widget-revision/version routes. | `grep -rn "trading-room/workspaces\|trading-room/proposals\|widget-revision-proposals\|getTradingRoomWorkspace\|workspaceId" execute-plans/src --include="*.ts" --include="*.tsx" \| grep -v "\.test\." \| grep -v types.ts` returns **zero matches**, same as both prior packets. |
| Workshop -> Trading Room handoff | `agora-main.tsx:87` mounts `<StrategyWorkshopPage workshopId={workshopId} />` with no `onAddToTradingRoom` prop, even though `onOpenWorkshop` (Trading Room -> Workshop) is wired one branch below it. | Confirmed identical at the same line (`execute-plans/src/entries/agora-main.tsx:87`). `onAddToTradingRoom` is still declared on `StrategyWorkshopPage`'s props and exercised only in its own test file. The most recent commit to touch this file (`eab6e0cfd`, PROD-003) did not add the wiring. |
| Widget allowlist / blocklist | 42-entry `widget_registry.v1.json`; shared `_FORBIDDEN_INTERACTIONS` (backend) / `BLOCKED_INTERACTION_KINDS` (frontend). | Confirmed present and unchanged in both `trading_room/router.py` and `dashboard/router.py` (backend) and `registry.ts` (frontend). See §3.1 for a registry-count self-correction. |
| SSE stream stub | `GET /bff/agora/trading-room/stream` is a self-documented stub. | Route and its stub comment still present at `trading_room/router.py:2811-2814`, still referenced only by a route-listing test (`test_trading_room.py:1172`), not a real streaming implementation. |

**Conclusion:** zero drift in the implementation surface itself. The parent
task (`AG-DYNUI-PROD-005`) has the exact same `last_update` timestamp
FOLLOWUP-2 already read — no branch, commit, or PR has been created for it at
any point.

### 3.1 Self-correction: widget registry count needed a second parse

An initial ad-hoc check in this follow-up used
`json.load(...).get("widgets", d)`, which silently fell back to the whole
top-level dict (4 keys: `registry_version`, `schema_version`, `created_at`,
`entries`) because the actual key is `entries`, not `widgets`. That produced a
spurious "4 widgets" reading. Re-parsing with the correct key
(`d["entries"]`) confirms the registry still holds **42 entries**, matching
both prior packets exactly. Recorded here so the parent owner does not see an
unexplained "4 vs 42" number if they re-run a similar one-liner without first
checking the JSON's actual top-level shape.

---

## 4. New Finding: The Dependency Chain Is Now Stalled Behind Two Independent Human Gates, Not Idle

FOLLOWUP-2 described `AG-DYNUI-PROD-002`/`AG-DYNUI-PROD-003` as
`review_approved` and moving. This follow-up's contribution is characterizing
*why* the chain has stopped advancing in the ~20 minutes of task time since:

1. **`AG-DYNUI-PROD-002`** is blocked on a **human-required self-merge
   approval** for `ajoe734/execute-plans` PR #171
   (`task/AG-DYNUI-PROD-002-agora-standalone-shell-compliant`). Independently
   confirmed via `gh pr view 171 --repo ajoe734/execute-plans`: `state=OPEN`,
   `mergeable=MERGEABLE`, `reviews=[]`. The supervisor has now re-checked this
   task for finalize-dispatch 10 times with no external state change and has
   flagged the cadence itself as a possible chair-review tuning item — this
   is not stuck because of missing work, it is stuck on a governance approval
   gate.
2. **`AG-DYNUI-PROD-003`** is blocked on **hosted screenshot evidence**
   that itself requires a human-approved `workflow_dispatch` deploy (see the
   sibling `AG-DYNUI-PROD-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` packet, which
   found the "port" work to the standalone `execute-plans` repo is not a
   mechanical cherry-pick). Ownership of this task itself changed
   (`Codex` → `Codex2`) since FOLLOWUP-2 due to a Codex usage-limit terminal,
   but the blocking condition (no hosted deploy yet) did not change.

**Practical meaning for the parent owner:** do not read `review_approved` on
`PROD-002`/`PROD-003` as "about to flip to `done`, so `PROD-005` can start
soon." Both are gated on Human/Ops actions (a self-merge approval and a
deploy dispatch, respectively) that neither task's owner can complete alone.
If `AG-DYNUI-PROD-005` needs to start before those land, the parent
owner/reviewer should confirm with Human/Ops whether `depends_on:
AG-DYNUI-PROD-002, AG-DYNUI-PROD-003` requires those tasks to be fully `done`,
or whether the already-merged/reviewed code state (both PRs' underlying
functional changes are merged and independently re-verified — see the
`AG-DYNUI-PROD-002`/`AG-DYNUI-PROD-003` `review_notes_zh` in `ai-status.json`)
is sufficient to unblock implementation work even while the task records
themselves remain open pending evidence/governance steps.

---

## 5. Consolidated Reader's Guide Across The Three Sidecar Packets

Three packets now exist for this parent task. To avoid the parent owner
re-deriving what is already established, here is what each contributes and
where to look first:

| Packet | Read this first if you need... |
|---|---|
| `AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF.md` (original) | The full backend route inventory (all in-scope V11 routes, idempotency/ETag/scope characteristics per route), the artifact-path corrections, the BFF query gap matrix, the operator journeys (A-G), the suggested frontend client method signatures and route path strings, and the "two distinct BFF surfaces" (`dashboard-recipes` vs `trading-room/workspaces`) design note. |
| `AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` | The Workshop -> Trading Room `onAddToTradingRoom` gap and the open scoping question of whether it belongs to this task. |
| `AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md` (this packet) | Confirmation that nothing in the implementation surface has moved since FOLLOWUP-2, and a concrete characterization of *why* the dependency chain is stalled (two independent human gates, not missing work) so the parent owner can decide whether to wait or to request the chain be unblocked. |

None of the three packets' factual claims contradict each other; each
narrows or re-confirms the prior one. The parent owner does not need to
re-run the full backend/frontend inventory from the original packet — only
the specific greps in §3 above, if more time has passed since this packet's
timestamp.

---

## 6. Parent Scope Boundary (unchanged from prior packets)

`AG-DYNUI-PROD-005` still owns:

- Adding the missing frontend BFF client functions for the V11
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
- `AG-DYNUI-PROD-002` (shell architecture; blocked on a human self-merge
  approval for `ajoe734/execute-plans` PR #171) and `AG-DYNUI-PROD-003`
  (default entry; blocked on a human-gated deploy dispatch for hosted
  screenshot evidence) — both remain upstream dependencies, not in-scope
  work, and neither is currently actionable by this sidecar or by
  `AG-DYNUI-PROD-005`'s owner directly.
- Whether the Workshop -> Trading Room `onAddToTradingRoom` handoff is
  in-scope is **still not decided** by the task brief — see FOLLOWUP-2 §4.2.
  This follow-up did not re-investigate the decision itself, only
  re-confirmed the gap still exists.

---

## 7. Reviewer Handoff

Reviewer (`Claude`) should verify:

1. This packet is support-only and made no change to canonical truth, BFF
   runtime, registry/governance code, or `execute-plans` frontend code.
2. §3's re-verification is accurate: re-run the greps if the worktree has
   moved since this packet's timestamp.
3. §3.1's self-correction is a fair, non-alarming note (an ad-hoc script bug
   on this sidecar's own side, not a real registry regression).
4. §4's characterization is accurate: PR #171 on `ajoe734/execute-plans` is
   genuinely open/mergeable/unreviewed and self-merge-blocked, and
   `AG-DYNUI-PROD-003`'s blocker is genuinely a pending hosted deploy, not
   missing implementation work.
5. §5's reader's guide fairly represents what each of the three packets
   contributes, without duplicating their content unnecessarily.
6. Parent (`AG-DYNUI-PROD-005`) is confirmed still unstarted (`todo`, no
   branch/PR, identical `last_update` to FOLLOWUP-2's read), so this packet
   remains a useful pre-implementation handoff rather than a stale one.

Recommended reviewer approval command:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/AG-DYNUI-PROD-005/AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md \
  REVIEW_NOTES_ZH="Support-only follow-up 3 核准：重新驗證前兩份 packet 的所有發現皆未變動（元件掛載、no-op callbacks、V11 client function 缺口、onAddToTradingRoom 缺口、widget allowlist/blocklist、SSE stub）；新增發現依賴鏈（PROD-002/PROD-003）目前卡在兩個獨立的人工審批關卡（execute-plans PR #171 self-merge、hosted deploy dispatch），而非缺少實作；並提供三份 sidecar packet 的統整導覽。未修改 canonical truth 或 runtime 檔案。" \
  ./scripts/ai-status.sh approve AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-3 \
  "Support-only AG-DYNUI-PROD-005 follow-up 3 approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-3 \
  "Describe the factual correction, ownership-boundary issue, or missing handoff detail needed before approval."
```

---

## 8. Validation Run

Commands run from this sidecar worktree:

```bash
git branch --show-current
# task/AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-3

git status --short

AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-3
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-005
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-002
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-003
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-004

git log --oneline -15 -- execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx \
  execute-plans/src/agora/dashboard/ execute-plans/src/agora/widgets/WidgetRevisionDrawer.tsx \
  execute-plans/src/lib/bff-v1/agora/tradingRoom.ts execute-plans/src/lib/bff-v1/agora/dashboard.ts \
  execute-plans/src/entries/agora-main.tsx execute-plans/src/routes/agora.tsx \
  services/control-plane/bff/agora/trading_room/
for h in eab6e0cfd 23a537ab7 d6065dec6 375ac2174 75a0e857c 76a622145 784c78a2d b72678e87 e909537eb a0e408213; do
  git log -1 --format="%h %ad %s" --date=iso "$h"
done

grep -rn "DashboardProposalPreview\|WidgetRevisionDrawer\|DashboardChangeLog" execute-plans/src --include="*.tsx" | grep -v "\.test\."
grep -n "onWidgetAdd\|onWidgetRemove\|onWidgetChartChange\|onPlacementsChange" execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx
grep -rn "trading-room/workspaces\|trading-room/proposals\|widget-revision-proposals\|getTradingRoomWorkspace\|workspaceId" execute-plans/src --include="*.ts" --include="*.tsx" | grep -v "\.test\." | grep -v types.ts
grep -rn "onAddToTradingRoom\|onOpenWorkshop" execute-plans/src
sed -n '75,100p' execute-plans/src/entries/agora-main.tsx

python3 - <<'PY'
import json
d = json.load(open('services/control-plane/specs/agora/widget_registry.v1.json'))
print(sorted(d.keys()), len(d["entries"]))
PY
grep -n "_FORBIDDEN_INTERACTIONS\|BLOCKED_INTERACTION_KINDS" -r services/control-plane/bff/agora execute-plans/src/agora
grep -n "trading-room/stream" -r services/control-plane/bff/agora

git log --all --oneline | grep -i "AG-DYNUI-PROD-005" | grep -v SIDECAR
gh pr list --repo ajoe734/pantheon --search "AG-DYNUI-PROD-005" --state all --json number,title,state,headRefName,url
gh pr list --repo ajoe734/execute-plans --search "AG-DYNUI-PROD-005" --state all --json number,title,state,headRefName,url
gh pr view 171 --repo ajoe734/execute-plans --json number,state,mergeable,reviews,url,headRefName
```

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned, per the task-scoped read-order instruction. No runtime, canonical,
registry, governance, or frontend change was made by this sidecar —
verification was read-only inspection of the worktree, `ai-status.json`
snapshots, and GitHub PR metadata on both `ajoe734/pantheon` and
`ajoe734/execute-plans`.
