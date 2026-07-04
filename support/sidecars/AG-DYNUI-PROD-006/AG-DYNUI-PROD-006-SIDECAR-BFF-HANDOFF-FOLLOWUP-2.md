# AG-DYNUI-PROD-006 BFF and Frontend Handoff Packet - Follow-up 2

| Field | Value |
|---|---|
| Parent task | `AG-DYNUI-PROD-006` |
| Parent title | Hosted Winner Branch E2E publish gate |
| Parent owner / reviewer | `Codex` / `Claude2` |
| Sidecar task | `AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Sidecar owner / reviewer | `Claude` / `Claude2` |
| Prior sidecar | `AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF` (`done`, PR #2869, packet at `support/sidecars/AG-DYNUI-PROD-006/AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF.md`) |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-04` |
| Mutates canonical | `false` |

This is a support artifact only. It does not change canonical truth, L1
contracts, BFF runtime code, `execute-plans` frontend code, registry behavior,
or governance behavior. The parent owner (`Codex`) and reviewer (`Claude2`)
decide whether and how to absorb this packet into the mainline closeout.

---

## 1. Why This Follow-up Exists

The original sidecar established a route inventory, a frontend-client gap
table, and a dependency snapshot, then closed `done`. Since then, the three
upstream dependencies (`AG-DYNUI-PROD-002`, `-003`, `-005`) have each grown
their own sidecar follow-up chains with concrete findings about *why* they
are not yet ready, rather than just *that* they are not done. This follow-up
does not re-derive the BFF route inventory or operator journey (unchanged,
see original packet §3/§5) — it re-verifies the parent's `todo` state and
consolidates the three dependency sidecars' newest findings into a single
readiness view for `AG-DYNUI-PROD-006`'s owner, because each finding
currently sits in a different task's sidecar file and none of them frames the
combined effect on the hosted E2E gate specifically.

---

## 2. Sources Read

| Source | Relevant finding |
|---|---|
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | This sidecar is `in_progress`, owner `Claude`, reviewer `Claude2`, `depends_on: AG-DYNUI-PROD-001, AG-DYNUI-PROD-004` (both `done`), `auto_created_by: supervisor-underutilization`. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-006` | Parent still `status: todo`, owner `Codex`, reviewer `Claude2`, `last_update: 2026-07-04T00:09:32Z` — unchanged since the original packet; no branch/commit/PR exists for the parent task itself. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-002` | `review_approved` (new since the original packet, which read it as `review`). Reviewer note: PR #171 independently re-verified (118 files / 1102 tests, `tsc --noEmit`, build, eslint all green); hosted desktop/mobile screenshots explicitly deferred to this task (`AG-DYNUI-PROD-006`); owner must not run `done` before that evidence exists. `next` field (7th re-check) confirms PR #171 is still `OPEN`/`CLEAN`/`MERGEABLE`, zero PR reviews, unmerged — blocked on AI self-merge governance (needs a human). |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-003` | Still `review_approved`; owner auto-reassigned `Codex` -> `Codex2` after a Codex quota terminal (no scope change). Reviewer note unchanged: hosted screenshot evidence still owed. |
| `support/sidecars/AG-DYNUI-PROD-003/AG-DYNUI-PROD-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` and `-FOLLOWUP-3.md` | New finding not in the original `AG-DYNUI-PROD-006` packet: the merged `AG-DYNUI-PROD-003` diff (`eab6e0cfd`) only exists in this pantheon repo's in-tree `execute-plans/` mirror; the standalone `ajoe734/execute-plans` repo's `dev` branch (HEAD `dd59740`, confirmed via `git -C /home/lupin/code/execute-plans fetch origin dev`) has **zero** matches for `TradingRoomDefaultEntry`/`selectDefaultReadyStrategy`, and a dry-run `git apply --check --3way` of the pantheon diff against that repo fails (missing `agora-main.tsx` entry point; 866/1087 diverged lines in `TradingRoomPage.tsx`). Porting is a scoped re-implementation, not a cherry-pick. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-005` | Still `status: todo`, owner `Claude`, reviewer `Codex2`, `last_update: 2026-07-04T00:09:32Z` — unchanged; no branch/commit/PR exists for the parent task itself. |
| `support/sidecars/AG-DYNUI-PROD-005/AG-DYNUI-PROD-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` | Re-confirms zero V11 client functions and zero mounted workspace/proposal/widget-revision components in `execute-plans/src`; adds a new finding that the Workshop -> Trading Room `onAddToTradingRoom` wiring gap (named "candidate: `AG-DYNUI-PROD-005`" by the `PROD-003` sidecars) is **not actually in `AG-DYNUI-PROD-005`'s written task brief** and recommends the parent owner/reviewer decide ownership explicitly rather than inherit it by inference. |
| `gh pr view 171 --repo ajoe734/execute-plans --json ...` | Confirms current state directly: `state: OPEN`, `mergeable: MERGEABLE`, `mergeStateStatus: CLEAN`, `reviews: []`, one passing check (`integration-gate`), base `dev`, head `task/AG-DYNUI-PROD-002-agora-standalone-shell-compliant`. Unlike `PROD-003`, this PR was opened directly against the standalone `ajoe734/execute-plans` repo — no in-tree-vs-standalone port gap exists for `PROD-002`; the only blocker is the human-merge-approval governance rule. |
| `curl https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json` | `commit=dd597405e0...`, `deployedAt=2026-07-04T01:20:41Z` — identical to the reading in the `PROD-003` follow-ups; no new deploy has occurred since, and it predates all three of `PROD-002`/`PROD-003`/`PROD-005`. |
| `curl https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health` | `{"status":"ok","service":"operator-bff","version":"0.2.0"}` — BFF unaffected by any of this. |
| `.orchestrator/approval-queue.json` (grep for `AG-DYNUI-PROD-002`/`-003`/`-006` and PR `171`) | No matching entries — no deploy-dispatch or PR-merge approval request for this chain is currently queued in the orchestrator's own approval system; the human actions named in the dependency sidecars have not yet been requested through that channel. |
| `git log --oneline -5 -- services/control-plane/bff/agora/trading_room/router.py execute-plans/src/lib/bff-v1/agora/tradingRoom.ts execute-plans/playwright.config.ts` | No commits since the original packet touched these paths (last touches are `AG-DYNUI-PROD-004` diagnostics and older auth fixes) — the route inventory and Playwright config claims in the original packet's §3/§8 are still accurate; re-grepped the versions/rollback routes directly to confirm. |
| `ls execute-plans/e2e/` | Still only `13-agora.spec.ts` for Agora; no new hosted E2E spec has been authored for `AG-DYNUI-PROD-006` yet. |

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned, per the task-scoped read-order instruction.

---

## 3. Consolidated Readiness View For The Hosted E2E Gate

This is the new contribution of this follow-up: none of the three dependency
sidecars frame their findings against what `AG-DYNUI-PROD-006` specifically
needs (a hosted, deployed instance of all three features). Read together:

| Dependency | Status | What is actually blocking it | Effort shape |
|---|---|---|---|
| `AG-DYNUI-PROD-002` (standalone shell) | `review_approved` | PR #171 already exists on the **real** `ajoe734/execute-plans` repo, is `MERGEABLE`/`CLEAN`, independently re-verified (tests/build/lint), and has zero open review comments. Purely blocked on a human merge decision (self-merge-without-human-approval governance rule — no code work remains). | Small: one human action (merge), then one human-gated deploy dispatch. |
| `AG-DYNUI-PROD-003` (default dynamic entry) | `review_approved` | The merged fix (`eab6e0cfd`) exists **only** in this pantheon repo's in-tree mirror. It was never ported to the real `ajoe734/execute-plans` repo, and a dry-run patch-apply against that repo's current `dev` fails on a missing entry-point file and 866/1087 diverged lines — this is a scoped re-implementation task, not a mechanical port (see `PROD-003` `FOLLOWUP-3` §3). | Larger: re-implementation work against a diverged file, then PR, then human merge, then deploy dispatch. |
| `AG-DYNUI-PROD-005` (dynamic workflow closeout) | `todo` | No branch, commit, or PR exists yet for the parent task. Zero V11 client functions, zero mounted workspace/proposal/widget-revision components. Additionally, whether the Workshop->Trading-Room handoff (`onAddToTradingRoom`) is in this task's scope is an open, undecided question (see `PROD-005` `FOLLOWUP-2` §4). | Largest: full feature implementation not yet started, plus an unresolved scope question. |

**Practical meaning for `AG-DYNUI-PROD-006`'s owner (`Codex`):** the three
blockers are not equivalent in kind. `PROD-002` is one governance approval
away from being deployable; `PROD-003` requires new frontend implementation
work against the standalone repo before it can even be merged there;
`PROD-005` has not started and has an open scope question that should be
settled before implementation, not after. Sequencing the hosted E2E
authoring/run around "wait for all three" without accounting for this spread
risks either idling on `PROD-002`'s trivial blocker or underestimating how
long `PROD-003`/`PROD-005` will actually take.

No canonical BFF/runtime gap was found in this pass beyond what the original
packet already documented (§3 there remains accurate — re-confirmed via the
`router.py` grep in §2 above).

---

## 4. Updated Recommendation For The Parent Owner

1. Do not block all hosted-proof work on `PROD-003`/`PROD-005` finishing
   first. `PROD-002`'s PR #171 can be merged and deployed independently
   (subject to the required human approvals for merge and for the
   `workflow_dispatch` deploy), giving an early hosted checkpoint for the
   standalone-shell + Strategy Workshop + "join Trading Room" portion of the
   journey (original packet §5 steps 1-2), even before `PROD-003`/`PROD-005`
   land.
2. Track `PROD-003`'s remaining work as a re-implementation against the
   standalone repo's current `TradingRoomPage.tsx`/`src/routes/agora.tsx`
   (per its `FOLLOWUP-3`), not a "port the diff" task — this affects how much
   lead time to allocate before requesting the next deploy dispatch.
3. Confirm with `PROD-005`'s owner/reviewer whether `onAddToTradingRoom` is
   in that task's scope before assuming the full Winner Branch journey
   (Workshop -> Trading Room -> proposal -> ... -> rollback) will be
   click-through reachable once `PROD-005` closes; if it is not wired there
   either, the hosted E2E in this task may still need a manual/deep-link step
   for that specific transition, or a fourth follow-up task.
4. Continue to expect **at least two more** human-gated `workflow_dispatch`
   deploy requests before the hosted E2E in this packet's §5/§8 (original
   packet) can be authored end to end: one after `PROD-002` merges, and at
   least one more after `PROD-003`/`PROD-005` merge (they can plausibly share
   a single deploy if their PRs land close together).
5. `.orchestrator/approval-queue.json` currently has no queued request for
   any of PR #171's merge or a deploy dispatch — these human actions have not
   yet been requested through the orchestrator's approval channel and will
   need to be initiated by parent ownership when ready.

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

Unchanged from the original packet — restated for this follow-up's own
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
2. §2/§3's re-verification is accurate: re-run `gh pr view 171 --repo
   ajoe734/execute-plans`, the `ai_status.py show` calls, and the
   `deployment.json` probe if state may have moved since this packet's
   timestamp.
3. §3's readiness table fairly characterizes the effort spread across
   `PROD-002`/`-003`/`-005` based on their own sidecars' findings, without
   overstepping into deciding those tasks' scope.
4. §4's recommendation (don't block all hosted proof on the slowest
   dependency; expect at least two deploy dispatches) is useful sequencing
   guidance for parent owner `Codex`, not an attempt to reassign or schedule
   work on his behalf.
5. Parent (`AG-DYNUI-PROD-006`) is confirmed still `todo` with no branch, so
   this remains a pre-implementation handoff rather than a stale one.

Recommended reviewer approval command:

```bash
AI_NAME=Claude2 REVIEW_FILE=support/sidecars/AG-DYNUI-PROD-006/AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md \
  REVIEW_NOTES_ZH="Support-only follow-up 2 核准：重新核實 AG-DYNUI-PROD-006 仍為 todo 且無 branch；彙整 PROD-002/003/005 三個 sidecar 的最新發現為單一 readiness 視角 -- PROD-002 (PR #171) 只差人工 merge 核准，PROD-003 需要在 standalone repo 重新實作而非 cherry-pick，PROD-005 尚未開始且 onAddToTradingRoom 歸屬未定；未修改 canonical truth 或 runtime 檔案。" \
  ./scripts/ai-status.sh approve AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 \
  "Support-only AG-DYNUI-PROD-006 follow-up 2 approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh reopen AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 \
  "Describe the factual correction or missing detail needed before approval."
```

---

## 8. Verification Performed For This Sidecar

```bash
git status --short
git branch --show-current
git fetch origin dev
git merge --ff-only origin/dev

AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-2
AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF
AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-006
AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-001
AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-002
AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-003
AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-004
AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-005

gh pr view 171 --repo ajoe734/execute-plans --json number,title,state,mergeable,mergeStateStatus,statusCheckRollup,autoMergeRequest,reviews,headRefName,baseRefName
gh pr list --repo ajoe734/pantheon --search "AG-DYNUI-PROD" --state all --json number,title,state,headRefName,url,mergedAt --limit 30
git ls-remote --heads origin 'task/AG-DYNUI-PROD-006*'

curl -sS --max-time 10 https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json
curl -sS --max-time 10 https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health

grep -o '"[^"]*AG-DYNUI-PROD-00[236][^"]*"' .orchestrator/approval-queue.json

grep -n "^export" execute-plans/src/lib/bff-v1/agora/tradingRoom.ts
git log --oneline -5 -- services/control-plane/bff/agora/trading_room/router.py execute-plans/src/lib/bff-v1/agora/tradingRoom.ts execute-plans/playwright.config.ts
grep -n "workspaces/{workspace_id}/versions\|versions/{version_id}/rollback" services/control-plane/bff/agora/trading_room/router.py
ls execute-plans/e2e/
```

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned, per the task-scoped read-order instruction. No runtime, canonical,
registry, governance, or frontend change was made by this sidecar —
verification was read-only inspection of the worktree, `ai-status.json`
snapshots, sibling sidecar packets, and anonymous/health-only GitHub/HTTP
probes. `dev` was fast-forward-merged into this task branch (no local commits
existed yet, so this was a clean fast-forward, not a rebase of owned work).
