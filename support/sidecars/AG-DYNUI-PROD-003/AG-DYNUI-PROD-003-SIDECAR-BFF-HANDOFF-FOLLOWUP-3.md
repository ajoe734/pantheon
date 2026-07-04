# AG-DYNUI-PROD-003 BFF and Frontend Handoff Packet - Follow-up 3

| Field | Value |
|---|---|
| Parent task | `AG-DYNUI-PROD-003` |
| Parent title | Trading Room default dynamic entry |
| Parent owner / reviewer | `Codex` / `Claude2` |
| Sidecar task | `AG-DYNUI-PROD-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` |
| Sidecar owner / reviewer | `Claude` / `Claude2` |
| Prior sidecars | `AG-DYNUI-PROD-003-SIDECAR-BFF-HANDOFF` (`done`), `AG-DYNUI-PROD-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` (`done`) |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-04` |
| Mutates canonical | `false` |

This is a support artifact only. It does not change canonical truth, L1
contracts, BFF runtime code, `execute-plans` frontend code, registry behavior,
or governance behavior. The parent owner (`Codex`) and reviewer (`Claude2`)
decide whether and how to absorb this packet into the mainline closeout.

---

## 1. Why This Follow-up Exists

`AG-DYNUI-PROD-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` (closed `done` at
`2026-07-04T01:45:46Z`) already re-confirmed that the parent's
hosted-screenshot closeout precondition is blocked because the merged feature
(`ec5d902fc` / head `eab6e0cfd`, PR #2860) has not been ported from this
pantheon repo's in-tree `execute-plans/` mirror to the standalone
`ajoe734/execute-plans` repo that the hosted dev FE actually deploys from.

This follow-up was auto-dispatched by the supervisor
(`auto_created_by: supervisor-underutilization`) while the parent
(`AG-DYNUI-PROD-003`) is still sitting in `review_approved` with the same
`next` note as before: hosted screenshot evidence still owed. Re-checking
first confirms the publish gap is **still open and materially unchanged**
(§2). Given that, this follow-up's added value is not re-stating the same
gap a third time — it is characterizing *why* "port the diff" is not a
mechanical cherry-pick, so the parent owner does not underestimate step 1 of
the FOLLOWUP-2 recommended closeout sequence.

---

## 2. Current Parent And Publish State (re-verified 2026-07-04)

| Check | Result |
|---|---|
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-003` | `status: review_approved`, owner `Codex`, reviewer `Claude2`, `last_update: 2026-07-04T01:02:25Z` — unchanged since FOLLOWUP-2. `next` still: "Reviewed and approved merged PR #2860; hosted screenshot evidence still owed before owner finalizes to done." |
| `gh pr list --repo ajoe734/execute-plans --search "trading-room" --state all` | Still no PR referencing `AG-DYNUI-PROD-003` or a default-entry/`TradingRoomDefaultEntry` change on the standalone repo. |
| `git -C /home/lupin/code/execute-plans fetch origin dev` then `log --oneline -8 origin/dev` | HEAD is still `dd59740` (PR #172, `PPL-EXEC-006`), same as FOLLOWUP-2's finding — no new commits landed on the standalone repo's `dev` since. |
| `git -C /home/lupin/code/execute-plans show origin/dev:src/agora/pages/trading-room/TradingRoomPage.tsx \| grep -n "TradingRoomDefaultEntry\|selectDefaultReadyStrategy"` | Zero matches — feature still absent. |
| `curl https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json` | `commit=dd597405e0...`, `deployedAt=2026-07-04T01:20:41Z` — identical snapshot to FOLLOWUP-2's read; no new deploy has occurred. |

**Conclusion:** no state change since FOLLOWUP-2. The gap is not stale
information going unrefreshed — it is a genuinely unmoved blocker on the
parent-owner side.

---

## 3. New Finding: The Port Is Not A Mechanical Patch (this follow-up's contribution)

FOLLOWUP-2 established *that* the standalone repo lacks the feature. This
follow-up went one step further and tested *how hard* porting it actually is,
without making any change to either repo.

### 3.1 Patch-apply attempt (read-only test, discarded)

Generated a patch from this repo's own merge (`git diff --relative=execute-plans/
28744d78d eab6e0cfd -- execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx
execute-plans/src/entries/agora-main.tsx execute-plans/src/agora/pages/trading-room/TradingRoomPage.test.tsx`)
and dry-ran `git apply --check --3way` against the standalone repo's
`origin/dev` checkout at `/home/lupin/code/execute-plans`. Result:

```
error: repository lacks the necessary blob to perform 3-way merge.
error: patch failed: src/agora/pages/trading-room/TradingRoomPage.tsx:682
error: src/agora/pages/trading-room/TradingRoomPage.tsx: patch does not apply
error: src/entries/agora-main.tsx: does not exist in index
```

No patch file or branch was created in the standalone checkout; this was a
dry-run only (`--check`), and the generated patch was left in the sidecar's
own scratch space, not committed anywhere.

### 3.2 Why it fails: two independent structural divergences

1. **No `agora-main.tsx` entry point in the standalone repo at all**
   (`find . -iname agora-main.tsx` → no result). The standalone repo wires
   `TradingRoomPage` through **React Router**, not tabs:
   `src/routes/agora.tsx` defines `AgoraTradingRoomRoute()`, which reads
   `strategyId` via `useParams` and passes
   `onBackToWorkshop={() => navigate("/agora/strategy-workshop")}` into
   `TradingRoomPage`. The pantheon in-tree mirror's `onOpenWorkshop` callback
   (`handleTabChange("strategy-workshop")`) has no tab system to call in the
   standalone repo — it needs to become
   `onOpenWorkshop={() => navigate("/agora/strategy-workshop")}` in
   `AgoraTradingRoomRoute`, following the exact pattern already used by the
   adjacent `onBackToWorkshop` prop one line above it.
2. **`TradingRoomPage.tsx` content has diverged independently.** Diffing this
   repo's pre-PROD-003 base (`28744d78d`, 1064 lines) against the standalone
   repo's current `origin/dev` file (1087 lines) shows **866 differing
   lines** — the standalone file already carries its own later features
   (grid editor PR #82, widget revision drawer PR #83/#84, auth/env fallback
   PR #168) that never existed in the pantheon in-tree mirror's history. The
   standalone file's `AggregateView`/`StrategyList` empty-shell component
   (confirmed still present at line ~621/565 in the current `origin/dev`
   file) is the same bug PROD-003 fixed, but it now lives inside a materially
   different surrounding file.

**Practical meaning for the parent owner:** step 1 of FOLLOWUP-2's
recommended sequence ("port the merged diff... open a PR") is not a
`git cherry-pick` or patch-apply task. It requires re-implementing
`TradingRoomDefaultEntry` / `selectDefaultReadyStrategy` and the
`onOpenWorkshop` wiring directly against the standalone repo's current
`TradingRoomPage.tsx` and `src/routes/agora.tsx`, informed by (but not copied
from) the pantheon in-tree diff. This is closer to a small independent
implementation task than a mechanical port, and should be scoped/estimated
that way rather than assumed to be a quick cherry-pick before requesting the
human-gated deploy dispatch.

---

## 4. Updated Gap Matrix

| Gap | FOLLOWUP-2 status | This follow-up's status |
|---|---|---|
| Publish to standalone `ajoe734/execute-plans` `dev` | Open — no PR found. | **Still open**, confirmed unchanged. |
| Hosted dev FE reflects the change | Open. | **Still open**, `deployment.json` unchanged since FOLLOWUP-2's read. |
| Nature of the port work | Assumed to be "port the diff... open a PR" (implies a mechanical cherry-pick). | **Clarified: not mechanical.** Patch-apply dry-run fails on both a missing entry-point file and 866/1087 diverged lines in the target file (§3). Treat as a scoped re-implementation, not a cherry-pick. |
| Workshop -> Trading Room `onAddToTradingRoom` wiring | Open, adjacent/downstream (`AG-DYNUI-PROD-005` candidate). | Unchanged; not re-investigated here. |
| Tie-break ordering for `selectDefaultReadyStrategy()` | Open, no written product spec found. | Unchanged; not re-investigated here (code-review question, not a publish-state question). |

---

## 5. Recommended Closeout Sequence For The Parent Owner (refined)

Same overall order as FOLLOWUP-2's §4, with step 1 refined per §3:

1. Re-implement the default-entry behavior directly in the standalone repo's
   `src/agora/pages/trading-room/TradingRoomPage.tsx` (using the pantheon
   in-tree diff at `eab6e0cfd` as a reference, not a literal patch source),
   and wire an `onOpenWorkshop` prop through `AgoraTradingRoomRoute` in
   `src/routes/agora.tsx` using `navigate("/agora/strategy-workshop")`,
   mirroring the existing `onBackToWorkshop` pattern. Open a PR on
   `ajoe734/execute-plans` (naming convention: `task/AG-DYNUI-PROD-003-...`,
   per `#171`/`#168`) and get it merged to that repo's `dev`.
2. Request a human-approved `workflow_dispatch` of `Pantheon Nonprod Deploy`
   (`environment=dev`) rather than waiting for the next nightly `publish/v*`
   cut.
3. Re-check `deployment.json` for a `commit` descending from the new PR's
   merge commit.
4. Capture hosted screenshots for the three default-entry states already
   documented in the original packet's §5 (zero strategies /
   strategies-none-ready / ready-strategy auto-entry), plus confirmation the
   BFF calls remain strict-live.
5. Attach that evidence to the parent task's `next`/`review_notes_zh` before
   running `AI_NAME=Codex ./scripts/ai-status.sh done AG-DYNUI-PROD-003 "..."`.

---

## 6. Parent / Reviewer Checklist

For `Claude2` (parent reviewer) and `Codex` (parent owner) to confirm before
`AG-DYNUI-PROD-003` moves to `done`:

- [ ] A PR exists and is merged on `ajoe734/execute-plans` carrying an
  equivalent default-entry behavior (re-implemented against the standalone
  repo's current file, not a literal cherry-pick — see §3).
- [ ] The `onOpenWorkshop` wiring in the standalone repo's
  `AgoraTradingRoomRoute` uses `navigate(...)`, matching the existing
  `onBackToWorkshop` convention, not a tab-switch call that does not exist in
  that repo.
- [ ] A human-approved `workflow_dispatch` deploy ran against `dev` after
  that merge, and `deployment.json` reflects a matching commit.
- [ ] Hosted screenshots exist for all three default-entry states, captured
  against the redeployed host.
- [ ] The `onAddToTradingRoom` gap is explicitly filed against
  `AG-DYNUI-PROD-005` (or an equivalent follow-up) rather than silently
  dropped.

---

## 7. Parent Boundary Notes

Unchanged from prior packets — restated for this follow-up's own
traceability:

Owned by `AG-DYNUI-PROD-003` parent (already implemented in `eab6e0cfd` on
this repo's in-tree mirror):

- default-entry branching logic in `TradingRoomPage.tsx`;
- `selectDefaultReadyStrategy()` and `TradingRoomDefaultEntry` component;
- `onOpenWorkshop` wiring in `agora-main.tsx` for the in-tree mirror's own
  tab-based entry point.

Not owned by this sidecar, and now more precisely scoped:

- re-implementing the equivalent behavior in the standalone
  `ajoe734/execute-plans` repo's `TradingRoomPage.tsx` and
  `src/routes/agora.tsx` (this is implementation work against a diverged
  file, not a patch/cherry-pick — see §3);
- requesting and completing the human-gated dev deploy dispatch;
- capturing hosted screenshot evidence;
- wiring `onAddToTradingRoom` from Workshop back into Trading Room
  (candidate: `AG-DYNUI-PROD-005`);
- BFF route/schema/registry/governance runtime changes — still none required
  for this feature.

This sidecar did not create, push, or open any branch/PR against
`ajoe734/execute-plans`; the patch-apply test in §3.1 was a local dry-run
only (`git apply --check`) and left no artifact in that checkout.

---

## 8. Verification Performed For This Sidecar

```bash
git status --short
git branch --show-current
AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-3
AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-003
AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-2
gh pr list --repo ajoe734/execute-plans --search "trading-room" --state all --json number,title,state,headRefName,url,mergedAt
gh pr list --repo ajoe734/execute-plans --state open --json number,title,state,headRefName,url
git -C /home/lupin/code/execute-plans fetch origin dev
git -C /home/lupin/code/execute-plans log --oneline -8 origin/dev
git -C /home/lupin/code/execute-plans show origin/dev:src/agora/pages/trading-room/TradingRoomPage.tsx | grep -n "TradingRoomDefaultEntry|selectDefaultReadyStrategy"
curl -sS --max-time 10 https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json
git log --oneline -8
git show ec5d902fc --stat
git diff --relative=execute-plans/ 28744d78d eab6e0cfd -- execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx execute-plans/src/entries/agora-main.tsx execute-plans/src/agora/pages/trading-room/TradingRoomPage.test.tsx > /tmp/ag-dynui-prod-003-port.patch
(cd /home/lupin/code/execute-plans && git apply --check --3way /tmp/ag-dynui-prod-003-port.patch)
find /home/lupin/code/execute-plans -iname "agora-main.tsx"
grep -rl "TradingRoomPage" /home/lupin/code/execute-plans/src --include=*.tsx | grep -v test
sed -n '1,30p' /home/lupin/code/execute-plans/src/routes/agora.tsx
git show 28744d78d:execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx > /tmp/pantheon-base-tsx
git -C /home/lupin/code/execute-plans show origin/dev:src/agora/pages/trading-room/TradingRoomPage.tsx > /tmp/standalone-current-tsx
wc -l /tmp/pantheon-base-tsx /tmp/standalone-current-tsx
diff /tmp/pantheon-base-tsx /tmp/standalone-current-tsx | wc -l
grep -n "onBackToWorkshop|onOpenWorkshop" /home/lupin/code/execute-plans/src/agora/pages/trading-room/TradingRoomPage.tsx /home/lupin/code/execute-plans/src/routes/agora.tsx
```

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned, per the task-scoped read-order instruction (a targeted
`grep -c "AG-DYNUI-PROD-003" ai-activity-log.jsonl` returned 0 in this
worktree, consistent with the project memory note that the worktree's log
mirror can lag the live store). No runtime, canonical, registry, governance,
frontend, or hosted-environment change was made by this sidecar. The
`/home/lupin/code/execute-plans` standalone checkout was only fetched and
read (`fetch`, `show`, `log`, a discarded `git apply --check` dry-run); no
commit, branch, or push was made there.

---

## 9. Reviewer Handoff

Reviewer (`Claude2`) should verify:

1. This packet is support-only and does not mutate canonical truth, runtime
   code, frontend code, route registry, or governance behavior, and made no
   change to the standalone `execute-plans` checkout beyond a discarded
   `git apply --check` dry-run.
2. §2's re-verification that the standalone repo's `dev` still lacks the
   PROD-003 feature is accurate (re-run the `grep` against `origin/dev` if
   the standalone repo has moved since this packet's timestamps).
3. §3's structural-divergence finding (no `agora-main.tsx`; router-based
   `AgoraTradingRoomRoute` with `onBackToWorkshop` precedent; 866/1087
   diverged lines in `TradingRoomPage.tsx`) is a legitimate scoping
   correction for the parent owner, not a sidecar overreach into implementing
   the port itself.
4. Parent (`Codex`) can use this packet to correctly estimate and sequence
   the remaining closeout work without treating it as review approval for
   `AG-DYNUI-PROD-003` itself — that approval was already recorded
   independently in `ai-status.json`.

No further sidecar-owned work is expected from `Claude` on this thread unless
the reviewer finds this handoff inaccurate or the publish state changes
materially.
