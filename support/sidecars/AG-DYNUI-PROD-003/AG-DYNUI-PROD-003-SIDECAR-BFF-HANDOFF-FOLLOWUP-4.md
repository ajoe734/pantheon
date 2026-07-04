# AG-DYNUI-PROD-003 BFF and Frontend Handoff Packet - Follow-up 4

| Field | Value |
|---|---|
| Parent task | `AG-DYNUI-PROD-003` |
| Parent title | Trading Room default dynamic entry |
| Parent owner / reviewer | `Claude` / `Claude2` |
| Sidecar task | `AG-DYNUI-PROD-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` |
| Sidecar owner / reviewer | `Claude2` / `Claude` |
| Prior sidecars | `AG-DYNUI-PROD-003-SIDECAR-BFF-HANDOFF` (`done`), `-FOLLOWUP-2` (`done`), `-FOLLOWUP-3` (`done`) |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-04` |
| Mutates canonical | `false` |

This is a support artifact only. It does not change canonical truth, L1
contracts, BFF runtime code, `execute-plans` frontend code, registry behavior,
or governance behavior. The parent owner (`Claude`) and reviewer (`Claude2`)
decide whether and how to absorb this packet into the mainline closeout.

---

## 1. Why This Follow-up Exists, And What It Adds

`FOLLOWUP-3` (closed `done`) found that porting the PROD-003 fix to the
standalone `ajoe734/execute-plans` repo was not a mechanical cherry-pick,
because the target file had independently diverged (866/1087 differing
lines) and the standalone repo has no `agora-main.tsx` tab-based entry point.

Since `FOLLOWUP-3` closed, the parent owner did that re-implementation work:
`ajoe734/execute-plans` PR #173 ("AG-DYNUI-PROD-003: port dynamic default
entry") now exists, is green, and has been sitting **unchanged for 16
re-verification passes** (per the parent's own `next` note) because merging
it requires a human — the AI owner/reviewer will not self-merge a PR it
authored and reviewed.

Re-running the same "is the gap still open" check a 17th time would be pure
churn with no new information (confirmed unchanged in §2 below, in one
pass, not sixteen). This follow-up's actual contribution is different: it
packages the **exact, low-friction human ask** so that whoever next has
GitHub write access to `ajoe734/execute-plans` can close this out in under a
minute, instead of having to reconstruct gate results and merge-safety
context from the CI run themselves. It also checks one thing none of the
prior 16 passes recorded: whether the block is a *technical* GitHub
restriction or purely a *policy* one.

---

## 2. Current State (re-verified 2026-07-04, one pass)

| Check | Result |
|---|---|
| `python3 scripts/ai_status.py show AG-DYNUI-PROD-003` (live store) | `status: review_approved`, owner `Claude`, reviewer `Claude2`. `next`: pass-16 note, unchanged content since pass 15. |
| `gh pr view 173 --repo ajoe734/execute-plans` | `OPEN`, `mergeable: MERGEABLE`, `mergeStateStatus: CLEAN`, head `2b054ab9f`, zero reviews, `autoMergeRequest: null` — identical to the parent's own last recorded check. |
| `gh api repos/ajoe734/execute-plans/branches/dev/protection` | **`404 Branch not protected`.** `dev` on the standalone repo has no branch-protection rule at all. |
| `gh api repos/ajoe734/execute-plans --jq '{allow_merge_commit,allow_squash_merge,allow_rebase_merge}'` | All three merge methods enabled on the repo. |
| Precedent: `gh pr view 168 --repo ajoe734/execute-plans` | Merged by human `ajoe734` (not a bot/agent), via a real 2-parent merge commit (`ffbc2357`, "Merge pull request #168 from..."), not a squash. Same convention PR #173 would use. |
| PR #173 diff stat | 3 files, +286/-84: `TradingRoomPage.tsx`, `TradingRoomPage.test.tsx`, `src/routes/agora.tsx`. No BFF, registry, or governance files touched. |
| PR #173 gate summary (`github-actions` bot comment, `2026-07-04T03:28:19Z`) | `Overall: WARN`. Gates 0/1/2/4/6/7 all `PASS`. Gates 3, 5, 8 `WARN`, each on a pre-existing, generically-owned item unrelated to this feature (`createDryRunEnabled=false`; `F10 Rollback Saga` expected-skip by standing release-gate exception; ungoverned `toast.success()` count repo-wide). Gate 7 ("Release Decision") itself is `PASS`: "0 failing or missing check(s)", "no exceptions needed". |

**Conclusion:** the gap is still open and unchanged from pass 16 — but it is
now clear the remaining blocker is **100% a self-imposed AI governance
policy, not a GitHub or CI restriction**. Nothing prevents a human with
write access to `ajoe734/execute-plans` from merging PR #173 right now.

---

## 3. Ready-to-Merge Ask (new artifact this follow-up contributes)

For whoever picks this up next — human or otherwise-authorized reviewer:

```bash
# Merge PR #173 on the standalone execute-plans repo (dev is unprotected;
# same merge-commit convention as PR #168):
gh pr merge 173 --repo ajoe734/execute-plans --merge
```

Why this is safe to run without re-deriving anything from the CI run:

- Scope is exactly the reviewed feature: `TradingRoomPage.tsx` (+ its test)
  and the `onOpenWorkshop` route wiring in `src/routes/agora.tsx`. No BFF,
  contract, registry, or governance file is touched (§2 diff stat).
- All hard gates (0, 1, 2, 4, 6, 7) pass; the three `WARN` items are
  pre-existing, generic, owned by `Codex`/`Claude` at the repo level, and
  are not regressions introduced by this PR.
- `mergeStateStatus: CLEAN`, no merge conflicts.
- Matches the established human-merge precedent (`ajoe734` merged #168 the
  same way).

After the merge, the remaining parent closeout steps (unchanged from
`FOLLOWUP-2`/`FOLLOWUP-3`, restated for completeness):

1. Request a human-approved `workflow_dispatch` of `Pantheon Nonprod Deploy`
   (`environment=dev`) so the redeploy does not wait for the nightly
   `publish/v*` cut.
2. Re-check `deployment.json` for a `commit` descending from PR #173's merge
   commit.
3. Capture hosted screenshots for the three default-entry states (zero
   strategies / strategies-none-ready / ready-strategy auto-entry) plus
   confirmation the BFF calls remain strict-live.
4. Attach that evidence to the parent task's `next` before running
   `AI_NAME=Claude ./scripts/ai-status.sh done AG-DYNUI-PROD-003 "..."`.

---

## 4. Parent / Reviewer Checklist

For `Claude` (parent owner) and `Claude2` (parent reviewer) to confirm
before `AG-DYNUI-PROD-003` moves to `done`:

- [ ] PR #173 is merged into `ajoe734/execute-plans` `dev` (by a human, or
  by an explicitly authorized non-self reviewer — not by the same AI
  identity that authored and reviewed it).
- [ ] A human-approved `workflow_dispatch` deploy ran against `dev` after
  that merge, and `deployment.json` reflects a matching commit.
- [ ] Hosted screenshots exist for all three default-entry states, captured
  against the redeployed host.
- [ ] The `onAddToTradingRoom` gap (Workshop -> Trading Room) remains
  explicitly filed against `AG-DYNUI-PROD-005` or an equivalent follow-up,
  as noted in `FOLLOWUP-2`/`FOLLOWUP-3`.

---

## 5. Parent Boundary Notes

Unchanged from prior packets — restated for this follow-up's own
traceability:

Owned by `AG-DYNUI-PROD-003` parent (already implemented, now in two
places: `eab6e0cfd` on this repo's in-tree mirror, and PR #173 head
`2b054ab9f` on the standalone repo):

- default-entry branching logic in `TradingRoomPage.tsx`;
- `selectDefaultReadyStrategy()` and `TradingRoomDefaultEntry` component;
- `onOpenWorkshop` wiring (tab-based in the in-tree mirror's `agora-main.tsx`,
  router-based via `navigate(...)` in the standalone repo's
  `AgoraTradingRoomRoute`).

Not owned by this sidecar:

- merging PR #173 (requires human or non-self-reviewer authorization —
  this sidecar did not merge it, only confirmed it is safe to merge);
- requesting and completing the human-gated dev deploy dispatch;
- capturing hosted screenshot evidence;
- wiring `onAddToTradingRoom` from Workshop back into Trading Room
  (candidate: `AG-DYNUI-PROD-005`);
- BFF route/schema/registry/governance runtime changes — still none
  required for this feature.

This sidecar made no change to `ajoe734/execute-plans` (no push, no merge,
no comment) and no change to canonical pantheon truth, runtime code,
frontend code, route registry, or governance behavior.

---

## 6. Verification Performed For This Sidecar

```bash
git status --short
git branch --show-current
python3 -c "import json; d=json.load(open('/home/lupin/code/pantheon/ai-status.json')); [print(t['id'],t.get('status'),t.get('owner'),t.get('reviewer'),t.get('last_update')) for t in d['tasks'] if 'AG-DYNUI-PROD-003' in t.get('id','')]"
gh pr view 173 --repo ajoe734/execute-plans --json number,title,state,mergeable,mergeStateStatus,headRefOid,statusCheckRollup,reviews,autoMergeRequest,url
gh pr view 173 --repo ajoe734/execute-plans --json body,comments --jq '.comments[] | {author: .author.login, createdAt: .createdAt}'
gh pr view 173 --repo ajoe734/execute-plans --json additions,deletions,changedFiles,files
gh pr view 168 --repo ajoe734/execute-plans --json mergedAt,mergeCommit,mergedBy,author,url
gh pr view 172 --repo ajoe734/execute-plans --json mergedAt,mergeCommit,mergeStateStatus
gh pr view 171 --repo ajoe734/execute-plans --json mergedAt,mergeCommit,state
gh api repos/ajoe734/execute-plans/branches/dev/protection
gh api repos/ajoe734/execute-plans --jq '{allow_merge_commit,allow_squash_merge,allow_rebase_merge,delete_branch_on_merge}'
git -C /home/lupin/code/execute-plans fetch origin dev
git -C /home/lupin/code/execute-plans log --format='%H %P' -1 ffbc2357f23b1a728ed6794d2231356ff28f16ed
```

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally
not scanned, per the task-scoped read-order instruction. Per project memory,
`scripts/ai_status.py` writes to `PANTHEON_STATUS_ROOT`
(`/home/lupin/code/pantheon`), not this worktree's local `ai-status.json`
copy, so state was read directly from the live store at that path. No
runtime, canonical, registry, governance, frontend, or hosted-environment
change was made by this sidecar; no push, comment, or merge was made against
`ajoe734/execute-plans` or `ajoe734/pantheon`.

---

## 7. Reviewer Handoff

Reviewer (`Claude`) should verify:

1. This packet is support-only and made no change to canonical truth,
   runtime code, frontend code, route registry, governance behavior, or the
   standalone `execute-plans` repo.
2. §2's branch-protection finding (`dev` on `ajoe734/execute-plans` returns
   `404 Branch not protected`) is accurate — re-run the `gh api` call if in
   doubt, since this is the load-bearing new fact this follow-up adds.
3. §3's merge command and safety rationale are accurate given PR #173's
   current diff scope and gate results (re-check if the PR has changed
   since this packet's timestamp).
4. Parent (`Claude`) can use this packet purely as a ready-to-hand-off ask
   for the next human touchpoint, not as review approval for
   `AG-DYNUI-PROD-003` itself — that approval is already recorded
   independently in `ai-status.json`.

No further sidecar-owned re-verification passes are expected on this thread
unless the reviewer finds this handoff inaccurate, PR #173 changes
materially, or the merge actually happens (at which point a follow-up should
confirm the deploy/screenshot steps in §3 instead of re-checking the merge
gap).
