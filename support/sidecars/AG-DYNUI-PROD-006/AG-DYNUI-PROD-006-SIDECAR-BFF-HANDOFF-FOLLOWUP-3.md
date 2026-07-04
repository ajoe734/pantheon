# AG-DYNUI-PROD-006 BFF and Frontend Handoff Packet - Follow-up 3

| Field | Value |
|---|---|
| Parent task | `AG-DYNUI-PROD-006` |
| Parent title | Hosted Winner Branch E2E publish gate |
| Parent owner / reviewer | `Codex` / `Claude2` |
| Sidecar task | `AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` |
| Sidecar owner / reviewer | `Claude` / `Claude2` |
| Prior sidecars | `AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF` (`done`, PR #2869), `AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` (`done`, PR #2879) |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-04` |
| Mutates canonical | `false` |

This is a support artifact only. It does not change canonical truth, L1
contracts, BFF runtime code, `execute-plans` frontend code, registry behavior,
or governance behavior. The parent owner (`Codex`) and reviewer (`Claude2`)
decide whether and how to absorb this packet into the mainline closeout.

---

## 1. Why This Follow-up Exists

`AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` gave the parent owner a
consolidated readiness view across `AG-DYNUI-PROD-002`/`-003`/`-005` as they
stood at that time, and flagged that the three blockers were not equivalent
in kind. Since then, `AG-DYNUI-PROD-003` and `AG-DYNUI-PROD-005` have each
produced their own `FOLLOWUP-3` sidecar packets with materially new findings
that sharpen (and in one case correct the effort estimate for) what
FOLLOWUP-2 already said. This follow-up re-verifies the parent's `todo` state,
folds those two new findings into the readiness view, and gives the parent
owner one place to read the current combined picture rather than re-deriving
it from three separate sidecar trees.

---

## 2. Sources Read

| Source | Relevant finding |
|---|---|
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` | This sidecar is `in_progress`, owner `Claude`, reviewer `Claude2`, `depends_on: AG-DYNUI-PROD-001, AG-DYNUI-PROD-004` (both `done`), `auto_created_by: supervisor-underutilization`. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-006` | Parent still `status: todo`, owner `Codex`, reviewer `Claude2`, `last_update: 2026-07-04T00:09:32Z` — byte-for-byte the same as FOLLOWUP-2's read; no branch/commit/PR exists for the parent task itself (confirmed again via `git ls-remote --heads origin 'task/AG-DYNUI-PROD-006*'`, which still shows only the two sidecar branches, not a parent branch). |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-002` | Still `review_approved`. `next` now records a re-check further along the same cadence FOLLOWUP-2 already noted: PR #171 (`ajoe734/execute-plans`) still `OPEN`/`MERGEABLE`/`CLEAN`, `integration-gate` still the only check and still `SUCCESS`, still zero reviews; blocked purely on the human self-merge-approval governance gate. Supervisor's own note recommends chair-review add dispatch backoff for this repeat-check pattern. |
| `gh pr view 171 --repo ajoe734/execute-plans --json ...` | Independently confirms the same: `state=OPEN`, `mergeable=MERGEABLE`, `mergeStateStatus=CLEAN`, `reviews=[]`, one passing `integration-gate` check, head `task/AG-DYNUI-PROD-002-agora-standalone-shell-compliant` — unchanged since FOLLOWUP-2. |
| `gh pr list --repo ajoe734/pantheon --search "AG-DYNUI-PROD-002" --state open` | Two additional **open, unmerged** pantheon-repo PRs exist for the `AG-DYNUI-PROD-002` task record itself: `#2867` ("correct execute-plans PR evidence" — points the task doc at PR #171 instead of the closed PR #170) and `#2872` ("record reviewer verification notes" — `Claude2`'s independent re-run of the full vitest suite, `tsc --noEmit`, build, and eslint against PR #171's commit, plus an explicit note that hosted screenshots are deferred to this parent task and the owner must not run `done` on source-only evidence). Neither changes the PR #171 merge blocker; both are pantheon-side documentation/evidence PRs still awaiting merge. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-003` | Still `review_approved`; owner reassigned `Codex` → `Codex2` after a Codex usage-limit terminal (no scope change; review notes on hosted-screenshot evidence still owed are unchanged). |
| `support/sidecars/AG-DYNUI-PROD-003/AG-DYNUI-PROD-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md` | **New finding not in the original packet or FOLLOWUP-2:** a dry-run `git apply --check --3way` of the merged pantheon diff (`eab6e0cfd`) against the standalone `ajoe734/execute-plans` repo's current `dev` fails outright — the standalone repo has no `agora-main.tsx` entry point at all (it wires `TradingRoomPage` through React Router's `AgoraTradingRoomRoute` instead of a tab system), and `TradingRoomPage.tsx` itself has 866/1087 diverged lines versus the pantheon in-tree base. The port is a scoped re-implementation against the standalone repo's current files, not a mechanical cherry-pick. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-005` | Still `status: todo`, owner `Claude`, reviewer `Codex2`, `last_update: 2026-07-04T00:09:32Z` — unchanged; no branch/commit/PR exists for the parent task itself. |
| `support/sidecars/AG-DYNUI-PROD-005/AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md` | **New finding:** independently characterizes the same `AG-DYNUI-PROD-002`/`-003` dependency chain as stalled behind **two distinct human gates** (a self-merge approval for PR #171, and a human-gated `workflow_dispatch` deploy that PROD-003 needs before hosted screenshots are possible) rather than missing implementation work, and recommends the parent owner confirm with Human/Ops whether `depends_on` requires full `done` or whether the already-merged/reviewed code state is enough to unblock downstream implementation. |
| `curl https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json` | `commit=dd597405e0...`, `deployedAt=2026-07-04T01:20:41Z` — identical to the reading in both the FOLLOWUP-2 packet and the `PROD-003`/`PROD-005` `FOLLOWUP-3` packets; no new deploy has occurred. |
| `curl https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health` | `{"status":"ok","service":"operator-bff","version":"0.2.0"}` — unaffected. |
| `grep -o '"[^"]*AG-DYNUI-PROD-00[236][^"]*"' .orchestrator/approval-queue.json` | Zero matches, same as FOLLOWUP-2 — no deploy-dispatch or PR-merge approval request for this chain is queued in the orchestrator's own approval system. |
| `git log --oneline -5 -- services/control-plane/bff/agora/trading_room/router.py execute-plans/src/lib/bff-v1/agora/tradingRoom.ts execute-plans/playwright.config.ts` | No commits since FOLLOWUP-2 touched these paths — the route inventory and Playwright config claims in the original packet's §3/§8 are still accurate. |
| `ls execute-plans/e2e/` | Still only `13-agora.spec.ts` for Agora; no new hosted E2E spec has been authored for `AG-DYNUI-PROD-006` yet. |
| `grep -n "^export" execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` | Still only `getTradingRoom`, `getTradingRoomStrategy`, `listDecisionEvents`, `getDecisionEvent`, `decideOnEvent` — the frontend-client gap documented in the original packet's §3.2 is unchanged. |

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned, per the task-scoped read-order instruction.

---

## 3. Updated Consolidated Readiness View For The Hosted E2E Gate

FOLLOWUP-2 built a readiness table from each dependency's status field.
This follow-up refines it with the concrete structural findings each
dependency's own `FOLLOWUP-3` has since produced, rather than restating them:

| Dependency | Status | What is actually blocking it | Effort shape (refined) |
|---|---|---|---|
| `AG-DYNUI-PROD-002` (standalone shell) | `review_approved` | PR #171 on `ajoe734/execute-plans` remains `MERGEABLE`/`CLEAN`, independently re-verified twice now (tests/build/lint), zero open review comments. Two additional pantheon-repo docs/evidence PRs (`#2867`, `#2872`) are also open but do not change the blocker. Purely a human merge decision (self-merge-without-human-approval governance rule). | Unchanged from FOLLOWUP-2: small — one human merge action, then one human-gated deploy dispatch. |
| `AG-DYNUI-PROD-003` (default dynamic entry) | `review_approved` | **Refined this follow-up:** confirmed via a discarded dry-run patch-apply that the fix cannot be cherry-picked — the standalone repo has no `agora-main.tsx` entry point (uses React Router's `AgoraTradingRoomRoute` instead) and its `TradingRoomPage.tsx` has 866/1087 lines diverged from the pantheon in-tree base (it already carries its own later grid-editor/widget-revision/auth features). | **Larger than FOLLOWUP-2 estimated:** a small independent re-implementation against the standalone repo's current files (informed by, not copied from, the pantheon diff), not a quick port. |
| `AG-DYNUI-PROD-005` (dynamic workflow closeout) | `todo` | No branch, commit, or PR exists yet. Zero V11 client functions, zero mounted workspace/proposal/widget-revision components (re-confirmed unchanged in its own `FOLLOWUP-3`). The `onAddToTradingRoom` scope question remains undecided. **New this follow-up:** its own `FOLLOWUP-3` independently arrived at the same two-human-gates characterization of the `PROD-002`/`PROD-003` chain as this packet, which cross-validates that reading rather than it being this packet's assumption alone. | Unchanged from FOLLOWUP-2: largest — full feature implementation not started, plus an unresolved scope question. |

**Practical meaning for `AG-DYNUI-PROD-006`'s owner (`Codex`):** the spread
FOLLOWUP-2 flagged is now sharper, not narrower. `PROD-002` remains one
governance approval away from deployable. `PROD-003`'s remaining work grew
from "port a diff" to "re-implement default-entry behavior and an
`onOpenWorkshop` navigation wiring directly against a materially different
file, following the existing `onBackToWorkshop` pattern as a precedent."
`PROD-005` has not moved. All three findings are now independently confirmed
by more than one sidecar lane (this packet, `PROD-003`'s own `FOLLOWUP-3`, and
`PROD-005`'s own `FOLLOWUP-3`), which increases confidence that the readiness
picture is accurate rather than a single sidecar's misreading.

No canonical BFF/runtime gap was found in this pass beyond what the original
packet already documented (§3 there remains accurate — re-confirmed via the
`router.py`/`tradingRoom.ts`/`e2e` checks in §2 above).

---

## 4. Updated Recommendation For The Parent Owner

1. Do not block all hosted-proof work on `PROD-003`/`PROD-005` finishing
   first — `PROD-002`'s PR #171 remains mergeable and independently
   re-verified twice; it can be merged and deployed as soon as a human
   approves, giving an early hosted checkpoint for the standalone-shell +
   Strategy Workshop + "join Trading Room" portion of the journey (original
   packet §5 steps 1-2) before `PROD-003`/`PROD-005` land.
2. Re-scope `PROD-003`'s remaining lead time upward: it is a small
   independent frontend implementation task against the standalone repo's
   current `TradingRoomPage.tsx`/`src/routes/agora.tsx`, not a mechanical
   cherry-pick — see `PROD-003`'s `FOLLOWUP-3` §3 for the exact wiring
   pattern to follow (`onOpenWorkshop` via `navigate(...)`, mirroring the
   existing `onBackToWorkshop` prop).
3. Confirm with `PROD-005`'s owner/reviewer whether `onAddToTradingRoom` is
   in that task's scope before assuming the full Winner Branch journey will
   be click-through reachable once `PROD-005` closes; this is still open.
4. Consider raising, or asking Human/Ops to clarify, whether this parent's
   `depends_on: AG-DYNUI-PROD-002, AG-DYNUI-PROD-003` (and, transitively,
   `AG-DYNUI-PROD-005`) requires those tasks to reach `done`, or whether the
   already-merged/independently-re-verified code state is sufficient to begin
   hosted E2E authoring for the portions that are ready, given that both
   `PROD-002` and `PROD-003` are gated on distinct human actions rather than
   remaining engineering work on their owners' side.
5. Continue to expect **at least two more** human-gated `workflow_dispatch`
   deploy requests before the hosted E2E in this packet's §5/§8 (original
   packet) can be authored end to end — this is unchanged from FOLLOWUP-2.
6. `.orchestrator/approval-queue.json` still has no queued request for PR
   #171's merge or a deploy dispatch — these human actions have not yet been
   initiated through the orchestrator's approval channel.

---

## 5. Test Location And Route Inventory (unchanged, re-confirmed only)

Re-ran the checks behind the original packet's §3 and §8 rather than
restating them in full:

- `services/control-plane/bff/agora/trading_room/router.py` still exposes the
  full route surface (proposal create/read/accept, workspace read/patch,
  views, widgets, widget-revision propose/accept including
  `keep_original_add_modified_copy`, version list, rollback) — no commits
  have touched this file since the original packet.
- `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` still only exports
  `getTradingRoom`, `getTradingRoomStrategy`, `listDecisionEvents`,
  `getDecisionEvent`, `decideOnEvent` — the frontend-client gap is unchanged
  and still squarely `AG-DYNUI-PROD-005`'s scope.
- `execute-plans/e2e/` still contains only `13-agora.spec.ts` for Agora
  coverage; `execute-plans/playwright.config.ts`'s `testDir: "./e2e"` claim
  is unchanged.

---

## 6. Parent Boundary Notes

Unchanged from prior packets — restated for this follow-up's own
traceability. This sidecar does not touch, and did not touch:

- `services/control-plane/bff/agora/trading_room/router.py` or any BFF
  runtime/route/schema file;
- `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` or any frontend
  client/page/component file;
- the standalone `ajoe734/execute-plans` repository (read-only `gh pr view`
  and hosted `deployment.json`/`health` probes only);
- `AG-DYNUI-PROD-002`, `-003`, or `-005` themselves (their own sidecars own
  those findings; this packet only cross-references and consolidates them
  for the `PROD-006` parent's benefit).

---

## 7. Reviewer Handoff

Reviewer (`Claude2`) should verify:

1. This packet is support-only and made no change to canonical truth, BFF
   runtime, registry/governance code, or `execute-plans` frontend code.
2. §2's re-verification is accurate: re-run `gh pr view 171 --repo
   ajoe734/execute-plans`, the `ai_status.py show` calls, and the
   `deployment.json` probe if state may have moved since this packet's
   timestamp.
3. §3's refined readiness table fairly reflects the new structural findings
   from `PROD-003`'s and `PROD-005`'s own `FOLLOWUP-3` packets, without
   overstepping into deciding those tasks' scope or implementation approach.
4. §4's recommendation (don't block all hosted proof on the slowest
   dependency; re-scope `PROD-003`'s lead time upward; consider clarifying
   the `depends_on` semantics with Human/Ops) is useful sequencing guidance
   for parent owner `Codex`, not an attempt to reassign or schedule work on
   his behalf.
5. Parent (`AG-DYNUI-PROD-006`) is confirmed still `todo` with no branch, so
   this remains a pre-implementation handoff rather than a stale one.

Recommended reviewer approval command:

```bash
AI_NAME=Claude2 REVIEW_FILE=support/sidecars/AG-DYNUI-PROD-006/AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md \
  REVIEW_NOTES_ZH="Support-only follow-up 3 核准：重新核實 AG-DYNUI-PROD-006 仍為 todo 且無 branch；將 PROD-003、PROD-005 各自 FOLLOWUP-3 的新發現併入 readiness 視角 -- PROD-002 (PR #171) 仍只差人工 merge 核准，PROD-003 的 port 經 dry-run 確認並非機械式 cherry-pick 而是需在 standalone repo 重新實作（工作量高於先前估計），PROD-005 的 FOLLOWUP-3 獨立得出相同的雙重人工關卡結論，交叉驗證了此讀法；未修改 canonical truth 或 runtime 檔案。" \
  ./scripts/ai-status.sh approve AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-3 \
  "Support-only AG-DYNUI-PROD-006 follow-up 3 approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh reopen AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-3 \
  "Describe the factual correction or missing detail needed before approval."
```

---

## 8. Verification Performed For This Sidecar

```bash
git status --short
git branch --show-current

AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-3
AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-006
AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-002
AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-003
AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-005

gh pr view 171 --repo ajoe734/execute-plans --json number,state,mergeable,mergeStateStatus,statusCheckRollup,autoMergeRequest,reviews,headRefName,baseRefName
gh pr list --repo ajoe734/pantheon --search "AG-DYNUI-PROD-002" --state open --json number,title,state,headRefName,url
gh pr view 2867 --repo ajoe734/pantheon --json title,body,state,mergeable
gh pr view 2872 --repo ajoe734/pantheon --json title,body,state,mergeable
gh pr list --repo ajoe734/pantheon --search "AG-DYNUI-PROD" --state all --json number,title,state,headRefName,url,mergedAt --limit 30
git ls-remote --heads origin 'task/AG-DYNUI-PROD-006*'

curl -sS --max-time 10 https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json
curl -sS --max-time 10 https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health

grep -o '"[^"]*AG-DYNUI-PROD-00[236][^"]*"' .orchestrator/approval-queue.json

grep -n "^export" execute-plans/src/lib/bff-v1/agora/tradingRoom.ts
git log --oneline -5 -- services/control-plane/bff/agora/trading_room/router.py execute-plans/src/lib/bff-v1/agora/tradingRoom.ts execute-plans/playwright.config.ts
ls execute-plans/e2e/
```

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned, per the task-scoped read-order instruction. No runtime, canonical,
registry, governance, or frontend change was made by this sidecar —
verification was read-only inspection of the worktree, `ai-status.json`
snapshots, sibling sidecar packets, and anonymous/health-only GitHub/HTTP
probes.
