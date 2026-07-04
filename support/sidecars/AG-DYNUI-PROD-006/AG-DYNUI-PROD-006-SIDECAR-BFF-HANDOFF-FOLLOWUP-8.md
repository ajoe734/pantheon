# AG-DYNUI-PROD-006 BFF and Frontend Handoff Packet - Follow-up 8

| Field | Value |
|---|---|
| Parent task | `AG-DYNUI-PROD-006` |
| Parent title | Hosted Winner Branch E2E publish gate |
| Parent owner / reviewer | `Codex` / `Claude2` |
| Sidecar task | `AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-8` |
| Sidecar owner / reviewer | `Claude` / `Claude2` |
| Prior sidecars | `AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF` (`done`, PR #2869), `-FOLLOWUP-2` (`done`, PR #2879), `-FOLLOWUP-3` (`done`, PR #2882/#2883), `-FOLLOWUP-4` (`done`, PR #2884), `-FOLLOWUP-5` (`done`, PR #2892/#2894), `-FOLLOWUP-6` (`done`, PR #2896/#2898/#2900), `-FOLLOWUP-7` (`done`) |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-04` |
| Mutates canonical | `false` |

This is a support artifact only. It does not change canonical truth, L1
contracts, BFF runtime code, `execute-plans` frontend code, registry behavior,
or governance behavior. The parent owner (`Codex`) and reviewer (`Claude2`)
decide whether and how to absorb this packet into the mainline closeout.

---

## 1. Why This Follow-up Actually Has New Information

`FOLLOWUP-6` and `FOLLOWUP-7` both found zero of five stated trigger
conditions true and recommended the supervisor stop dispatching further
same-shape sidecars unless one of these fired:

1. execute-plans PR #171 or #173 merges;
2. hosted `deployment.json` changes away from `dd597405...`;
3. a real `AG-DYNUI-PROD-005` implementation branch/PR appears;
4. parent `AG-DYNUI-PROD-006` status/branch changes;
5. a BFF route or frontend workflow surface actually changes.

This `FOLLOWUP-8` re-verified all five conditions and found **two of them
true for the first time**: both PR #171 and PR #173 merged, and the hosted
`deployment.json` moved off `dd597405...`. That is real delta worth recording
precisely, and it changes part of the dependency picture below — though not
the part that actually gates the parent's hosted E2E.

---

## 2. Sources Read And Current Findings

| Source | Finding |
|---|---|
| `.orchestrator/task-briefs/ag_dynui_prod_006_sidecar_bff_handoff_followup_8.md` | Scope is support-only: prepare BFF/frontend handoff materials for `AG-DYNUI-PROD-006`; do not modify canonical truth. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-8` | Sidecar is `in_progress`, owner `Claude`, reviewer `Claude2`, artifact path is this packet. |
| `gh pr view 171 --repo ajoe734/execute-plans` | **Changed.** `state: MERGED`, `mergedAt: 2026-07-04T12:14:50Z`, merge commit `467d930957bf109405fa50a5bc252ff66ec3a7ee`. |
| `gh pr view 173 --repo ajoe734/execute-plans` | **Changed.** `state: MERGED`, `mergedAt: 2026-07-04T12:22:37Z`. |
| Hosted FE `deployment.json` (`curl .../deployment.json`) | **Changed.** `commit: 467d930957bf109405fa50a5bc252ff66ec3a7ee`, `sourceBranch: dev`, `deployedAt: 20260704T121701Z` — moved off `dd597405e014cc91cf73f4ea2e96a561fcbf9c61`. This deploy timestamp (12:17:01Z) is *between* the PR #171 merge (12:14:50Z) and the PR #173 merge (12:22:37Z), and the commit hash matches PR #171's merge commit exactly — so the currently-served hosted FE contains PR #171 (`PROD-002`) but not yet PR #173 (`PROD-003`). |
| `gh api repos/ajoe734/execute-plans/compare/467d930957bf109405fa50a5bc252ff66ec3a7ee...dev` | `{"ahead_by": 2, "behind_by": 0, "status": "ahead"}` — confirms `dev` (which now includes PR #173) is 2 commits ahead of the deployed commit. |
| `gh run list --repo ajoe734/execute-plans` | A `Pantheon Dev FE Deploy` workflow run (`databaseId 28706071377`) triggered by the `dev` push at `12:22:40Z` (right after PR #173 merged) is **`in_progress`** at the time this packet was written — it is the deploy that should bring PR #173 onto the hosted FE. This packet does not wait for it to finish; it records the state observed. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-002` | Still `review_approved`, owner `Claude`, reviewer `Claude2`. Owner's own `next` note (Cycle 93) independently confirms PR #171 merged and that this resolves the prior self-merge governance blocker; task remains `review_approved` pending `AG-DYNUI-PROD-006` hosted screenshot evidence — owner explicitly states no `done` action was taken this cycle. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-003` | Still `review_approved`, owner `Claude`, reviewer `Claude2`. Reviewer note (from PR #2860 review) still requires live no-strategy/ready-strategy screenshot evidence via human-gated deploy before finalize; that requirement is about a *Pantheon-repo* screenshot deploy step, separate from the execute-plans PR #173 merge just observed. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-005` | **Unchanged.** Still `todo`, owner `Claude`, reviewer `Codex2`, `last_update: 2026-07-04T00:09:32Z` — no implementation has started. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-006` | **Unchanged.** Still `todo`, owner `Codex`, reviewer `Claude2`, `last_update: 2026-07-04T00:09:32Z`; no parent task branch/PR exists. |
| BFF/frontend inventory re-check (`grep ^export execute-plans/src/lib/bff-v1/agora/tradingRoom.ts`; `grep ... services/control-plane/bff/agora/trading_room/router.py`) | **Unchanged.** Backend routes for proposals, widget-revision-proposals, versions, and rollback remain present in `router.py`; `tradingRoom.ts` still exports only `getTradingRoom`, `getTradingRoomStrategy`, `listDecisionEvents`, `getDecisionEvent`, `decideOnEvent` — no proposal/workspace/widget-revision/version/rollback client wrappers yet. |
| `execute-plans/e2e/` listing | **Unchanged.** `13-agora.spec.ts` remains the only Agora Playwright spec; no new hosted E2E spec exists for the flow this parent task needs. |
| Hosted BFF health (`curl .../health`) | `operator-bff` healthy, version `0.2.0`. |

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned, per the task-scoped read-order instruction.

---

## 3. Trigger Condition Status (All Five, Re-checked)

| Trigger from `FOLLOWUP-6`/`FOLLOWUP-7` | Status now | What actually changed |
|---|---|---|
| 1. execute-plans PR #171 or #173 merges | **FIRED.** Both merged: #171 at `12:14:50Z`, #173 at `12:22:37Z`. | `PROD-002` and `PROD-003` frontend code is on `execute-plans:dev`. Neither task itself moved to `done` — both remain `review_approved` pending `AG-DYNUI-PROD-006` hosted screenshot evidence, per their own reviewer notes. |
| 2. hosted `deployment.json` changes away from `dd597405...` | **FIRED.** Now `467d930957bf109405fa50a5bc252ff66ec3a7ee`, deployed `20260704T121701Z`. | Hosted FE now serves PR #171's code. It does **not yet** serve PR #173's code — that deploy is `in_progress` as of this packet (§2). |
| 3. a real `AG-DYNUI-PROD-005` implementation branch/PR appears | **NOT fired.** | `PROD-005` is still `todo`, unowned by any active branch or PR. |
| 4. parent `AG-DYNUI-PROD-006` status/branch changes | **NOT fired.** | Parent unchanged: `todo`, no branch. |
| 5. a BFF route or frontend workflow surface actually changes | **NOT fired.** | `tradingRoom.ts` export set and `router.py` route set are byte-for-byte the same shape `FOLLOWUP-6`/`-7` recorded — the merged PRs #171/#173 changed shell/routing code (`PROD-002`/`PROD-003` scope), not the proposal/grid/widget-revision/version/rollback BFF client surface, which is `PROD-005` scope. |

Two of five triggers fired. This justifies writing this follow-up instead of
declining it as a repeat no-op. It does **not** mean the parent's hosted E2E
is newly unblocked — see §4.

---

## 4. Updated Readiness View

| Dependency | Current state | Remaining blocker |
|---|---|---|
| `AG-DYNUI-PROD-002` | `review_approved`; execute-plans PR #171 **merged** into `dev` (`467d930957bf109405fa50a5bc252ff66ec3a7ee`) and now served by the hosted FE. | No longer blocked on human merge. Still blocked on this parent task (`AG-DYNUI-PROD-006`) supplying hosted desktop/mobile screenshots before owner can run `done`. |
| `AG-DYNUI-PROD-003` | `review_approved`; execute-plans PR #173 **merged** into `dev`, but the hosted FE deploy for that push was still `in_progress` at packet time — hosted FE had not yet caught up to it. | No longer blocked on human merge. Still needs (a) the in-flight deploy to finish and (b) live no-strategy/ready-strategy screenshot evidence before owner can finalize. |
| `AG-DYNUI-PROD-005` | Still `todo`; no implementation branch/PR in either repo; unchanged since `FOLLOWUP-2`. | This remains the hard prerequisite: it wires `tradingRoom.ts` and the workspace components to the proposal/grid/widget-revision/version/rollback routes that `router.py` already exposes. Nothing in this cycle's merges touches this scope. |
| `AG-DYNUI-PROD-006` (parent) | Still `todo`; no direct branch/PR. | Even with `PROD-002`/`PROD-003` merged and (mostly) deployed, the hosted E2E in this parent task cannot exercise proposal preview, accept, grid edit, widget revision, version history, or rollback until `AG-DYNUI-PROD-005` lands. The Strategy Workshop → readiness → join Trading Room portion of the journey (packet §5 steps 1-2 in the base `AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF.md`) is now closer to authorable against a real hosted deploy, but the full flow is not. |

The dependency chain's *shape* is unchanged from `FOLLOWUP-6`/`-7`: `PROD-005`
is still the single hard blocker for the full hosted E2E, and the parent
still has no branch. What changed is that the upstream merge/deploy
sequencing for `PROD-002`/`PROD-003` — previously stalled on a human
merge — has now cleared, narrowing the remaining gap to `PROD-005` plus the
in-flight `PROD-003` deploy catching up.

---

## 5. Parent Handoff Guidance

For parent owner `Codex`, the critical path is now shorter than in
`FOLLOWUP-6`/`-7`:

1. confirm the `Pantheon Dev FE Deploy` run for the PR #173 push
   (`execute-plans` `databaseId 28706071377`, started `12:22:40Z`) completes
   and that `deployment.json` subsequently reports a commit that is an
   ancestor-or-equal of `execute-plans:dev` HEAD (`691f2ec56af9...` at
   packet time), so both `PROD-002` and `PROD-003` are actually served;
2. start, merge, and deploy `PROD-005` for the strict BFF-backed V11
   workflow wiring — this is now the only remaining implementation gap
   blocking the full flow;
3. only then author/run `PROD-006` hosted desktop/mobile E2E against the
   deployed FE + live BFF, and feed the resulting screenshots back into
   `PROD-002`/`PROD-003` closeout as their reviewers already require.

The BFF route inventory, gap matrix, and operator journey script from the
original packet (`AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF.md`, §3 and §5)
remain complete and unchanged; there is still no unresearched BFF surface
for a further handoff packet to document.

**Recommendation to the supervisor:** do not dispatch
`AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-9` merely because a worker is
idle. A further follow-up is justified only if one of these fires:

- the `PROD-003` deploy catch-up completes and hosted `deployment.json`
  moves to a commit that is `dev` HEAD or later (i.e. serves both #171 and
  #173);
- a real `AG-DYNUI-PROD-005` implementation branch/PR appears;
- parent `AG-DYNUI-PROD-006` status/branch changes;
- a BFF route or frontend workflow surface actually changes (i.e. `PROD-005`
  scope lands).

If underutilized capacity needs work in this lane, the higher-value use of
that capacity is picking up `AG-DYNUI-PROD-005` implementation directly
rather than researching it again — it is unowned-by-active-work and is now
the sole remaining implementation blocker on this critical path.

---

## 6. Reviewer Handoff

Reviewer (`Claude2`) should verify:

1. This packet is support-only and made no change to canonical truth, BFF
   runtime, registry/governance code, or frontend code.
2. §3 correctly identifies that triggers 1 and 2 (PR #171/#173 merge, hosted
   deploy commit change) fired for the first time, while triggers 3-5 did
   not.
3. §4 correctly states `AG-DYNUI-PROD-005` remains the sole hard blocker for
   the full hosted E2E, and that the `PROD-003` deploy catch-up was still
   in-flight at packet time (not yet confirmed complete).
4. §5's updated recommendation (narrower critical path, still no
   `FOLLOWUP-9` without a new trigger) is a reasonable reaction to this
   cycle's real delta.

Recommended reviewer approval command:

```bash
AI_NAME=Claude2 REVIEW_FILE=support/sidecars/AG-DYNUI-PROD-006/AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-8.md \
  REVIEW_NOTES_ZH="Support-only follow-up 8 核准：此 packet 沒有修改 canonical truth/runtime/frontend code；獨立核實 execute-plans PR #171 與 PR #173 皆已 MERGED，hosted deployment.json 已從 dd597405 移動到 467d930957bf109405fa50a5bc252ff66ec3a7ee（僅涵蓋 PR171，PR173 的 deploy 當下仍 in_progress）。PROD-005 仍 todo 無實作，維持唯一硬性 blocker；parent PROD-006 仍 todo 無 branch。同意 §5 更新後的建議：不要僅因 worker 閒置就派發 FOLLOWUP-9，除非其中一個新 trigger 觸發。" \
  ./scripts/ai-status.sh approve AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-8 \
  "Support-only AG-DYNUI-PROD-006 follow-up 8 approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh reopen AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-8 \
  "Describe the factual correction or missing handoff detail needed before approval."
```

---

## 7. Verification Performed For This Sidecar

Commands run from this sidecar worktree unless an absolute path is shown:

```bash
git status --short
git branch --show-current

AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-8
AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-006
AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-002
AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-003
AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-005

gh pr view 171 --repo ajoe734/execute-plans --json number,title,state,mergeable,mergeStateStatus,statusCheckRollup,autoMergeRequest,reviews,mergedAt
gh pr view 173 --repo ajoe734/execute-plans --json number,title,state,mergeable,mergeStateStatus,statusCheckRollup,autoMergeRequest,reviews,mergedAt

curl -sS --max-time 10 https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json
curl -sS --max-time 10 https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health

gh api repos/ajoe734/execute-plans/commits/dev --jq '.sha'
gh api repos/ajoe734/execute-plans/compare/467d930957bf109405fa50a5bc252ff66ec3a7ee...dev --jq '{ahead_by, behind_by, status}'
gh run list --repo ajoe734/execute-plans --limit 8 --json databaseId,name,status,conclusion,createdAt,headBranch,event
gh run view 28706071377 --repo ajoe734/execute-plans --json status,conclusion,createdAt

grep -n "^export" execute-plans/src/lib/bff-v1/agora/tradingRoom.ts
grep -n "workspaces/{workspace_id}/versions\|versions/{version_id}/rollback\|widget-revision-proposals\|trading-room/proposals" services/control-plane/bff/agora/trading_room/router.py
ls execute-plans/e2e
```

No runtime, canonical, registry, governance, frontend, or BFF implementation
files were changed by this sidecar.
