# AG-DYNUI-PROD-003 BFF and Frontend Handoff Packet - Follow-up 2

| Field | Value |
|---|---|
| Parent task | `AG-DYNUI-PROD-003` |
| Parent title | Trading Room default dynamic entry |
| Parent owner / reviewer | `Codex` / `Claude2` |
| Sidecar task | `AG-DYNUI-PROD-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Sidecar owner / reviewer | `Claude` / `Claude2` |
| Prior sidecar | `AG-DYNUI-PROD-003-SIDECAR-BFF-HANDOFF` (`review_approved` -> `done`, packet at `support/sidecars/AG-DYNUI-PROD-003/AG-DYNUI-PROD-003-SIDECAR-BFF-HANDOFF.md`) |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-04` |
| Mutates canonical | `false` |

This is a support artifact only. It does not change canonical truth, L1
contracts, BFF runtime code, `execute-plans` frontend code, registry behavior,
or governance behavior. The parent owner (`Codex`) and reviewer (`Claude2`)
decide whether and how to absorb this packet into the mainline closeout.

---

## 1. Why This Follow-up Exists

The prior sidecar packet (closed `done`) already flagged two open gaps while
`AG-DYNUI-PROD-003` was still in `review`:

1. the merged implementation (`ec5d902fc` / head `eab6e0cfd`, PR #2860) lives
   only in this pantheon repo's in-tree `execute-plans/` mirror, not on the
   standalone `ajoe734/execute-plans` repo that the hosted dev FE actually
   deploys from;
2. the Workshop -> Trading Room `onAddToTradingRoom` callback is still not
   wired in `agora-main.tsx`.

Since that packet closed, the parent moved from `review` to `review_approved`
(`Claude2` approved PR #2860 on `2026-07-04T01:02:25Z`: 51/51 Trading Room
tests, 42/42 unchanged-file regression tests, `build:agora` green, no
hardcoded data). `Claude2`'s approval note explicitly adds a new closeout
condition, quoted verbatim from `ai-status.json`:

> 收尾前提醒 owner：acceptance 要求的 live screenshot evidence (no-strategy /
> ready-strategy) 尚未附上，需在 finalize 為 done 前補齊（比照
> AG-DYNUI-PROD-004 前例，需 human-gated dev deploy dispatch）。

This follow-up exists to re-verify, with live evidence gathered on
`2026-07-04`, whether that hosted-screenshot precondition can currently be
satisfied — and the answer is **not yet**, for a concrete, previously-flagged
reason: the feature still has not reached the real `execute-plans` repo.

---

## 2. Current Parent And Publish State (re-verified 2026-07-04)

| Check | Result |
|---|---|
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-003` | `status: review_approved`, owner `Codex`, reviewer `Claude2`. `next`: "Reviewed and approved merged PR #2860; hosted screenshot evidence still owed before owner finalizes to done." |
| `gh pr view 2860 --repo ajoe734/pantheon` | `MERGED` at `2026-07-04T00:51:28Z`, merge commit `ec5d902fce715dbfb2254641ae86825130c4cddd`, head `task/AG-DYNUI-PROD-003`, target `dev`. Files: `TradingRoomPage.tsx`, `TradingRoomPage.test.tsx`, `agora-main.tsx` — all inside this pantheon repo's in-tree `execute-plans/` mirror. |
| `gh pr list --repo ajoe734/execute-plans --search "trading-room"` and a direct `AG-DYNUI-PROD-003` name search | No open, merged, or closed PR on the standalone repo mentions `AG-DYNUI-PROD-003` or a Trading Room default-entry change. The only currently open Trading-Room-adjacent PR is `#171` (`AG-DYNUI-PROD-002`, standalone shell), a different feature. |
| `git -C /home/lupin/code/execute-plans fetch origin dev` then `git show origin/dev:src/agora/pages/trading-room/TradingRoomPage.tsx \| grep -n "TradingRoomDefaultEntry\|selectDefaultReadyStrategy"` | **Zero matches.** The real repo's `dev` branch does not contain the default-entry feature at all. |
| `git -C /home/lupin/code/execute-plans show origin/dev:src/entries/agora-main.tsx \| grep -n "onAddToTradingRoom\|onOpenWorkshop"` | Zero matches for either — confirms the real repo predates both the PROD-003 change and the still-open Workshop-join gap. |
| `curl -fsS https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json` | `commit`/`sourceRef` = `dd597405e014cc91cf73f4ea2e96a561fcbf9c61`, `deployedAt` = `2026-07-04T01:20:41Z`, `sourceBranch=dev`, `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`. This matches the real repo's current `origin/dev` HEAD (`dd59740`, merging PR #172 "PPL-EXEC-006: align persona fleet paper runtime UI") — an unrelated feature, deployed **after** PROD-003 merged into pantheon's `dev`, but it still does not carry the PROD-003 change because that change was never ported to the real repo. |
| `curl -fsS https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health` | `{"status":"ok","service":"operator-bff","version":"0.2.0"}` — BFF is healthy and unaffected by this gap. |
| `.github/workflows/nonprod-deploy.yml` trigger block | Confirms `dev` only auto-redeploys on a `push` to `publish/v*` (nightly cut) or via `workflow_dispatch`; a plain merge to `execute-plans`' `dev` branch does **not** by itself trigger a redeploy. This matches the reviewer's citation of the `AG-DYNUI-PROD-004` precedent needing a human-gated dispatch. |

**Conclusion:** the hosted-screenshot precondition is blocked one step earlier
than a deploy-dispatch approval. Even a human-approved `workflow_dispatch`
right now would deploy real-repo `dev` HEAD (`dd59740`), which still lacks
`TradingRoomDefaultEntry`. The dependency order is: **port to
`ajoe734/execute-plans` first, then dispatch deploy, then capture hosted
screenshots** — not deploy-then-screenshot as the review note's phrasing might
suggest in isolation.

---

## 3. Updated Gap Matrix

No new canonical BFF contract is needed; this section only updates the
publish-readiness status from the original packet's §4.

| Gap | Original packet status (`2026-07-04`, pre-approval) | Current status (`2026-07-04`, post-approval, this follow-up) |
|---|---|---|
| Publish to standalone `ajoe734/execute-plans` `dev` | Open — no PR found. | **Still open.** Directly confirmed by content diff against `origin/dev`; no PR exists on the standalone repo. |
| Hosted dev FE reflects the change | Open — `deployment.json` predated the change. | **Still open**, and the hosted FE has since redeployed once more (to `dd59740`) without picking up the change, because the source repo still lacks it. |
| Workshop -> Trading Room `onAddToTradingRoom` wiring | Open, named as adjacent/downstream (`AG-DYNUI-PROD-005` candidate). | **Unchanged** — confirmed absent in both the pantheon in-tree mirror and the real repo's `origin/dev`. Not a PROD-003 blocker; still worth tracking against `AG-DYNUI-PROD-005`. |
| Tie-break ordering for `selectDefaultReadyStrategy()` | Open, no written product spec found. | Unchanged; not re-investigated in this follow-up since it is a code-review question, not a publish-state question. |

---

## 4. Recommended Closeout Sequence For The Parent Owner

This is guidance only; the sidecar does not perform any of these steps itself
because they require editing the standalone `execute-plans` frontend repo,
requesting a human-gated deploy dispatch, and capturing hosted screenshots —
all outside this sidecar's support-only scope.

1. Port the merged diff (`git diff 28744d78d eab6e0cfd -- execute-plans/...`
   inside this repo) onto a new branch of the real `ajoe734/execute-plans`
   repo, open a PR there (following the `#171`/`#168` naming convention:
   `task/AG-DYNUI-PROD-003-...`), and get it merged to that repo's `dev`.
2. Request a human-approved `workflow_dispatch` of `Pantheon Nonprod Deploy`
   (`environment=dev`) so the redeploy is not left waiting for the next
   nightly `publish/v*` cut.
3. Re-check `deployment.json` for a `commit` that is a descendant of the new
   real-repo PR's merge commit.
4. Capture hosted screenshots for the three default-entry states already
   documented in the original packet's §5 (Journeys A/B/C: zero strategies,
   strategies-none-ready, ready-strategy auto-entry), plus network evidence
   that `/bff/agora/trading-room` responses remain strict-live (no fixture
   fallback).
5. Attach that evidence to the parent task's `next`/`review_notes_zh` (or a
   dedicated evidence doc) before running
   `AI_NAME=Codex ./scripts/ai-status.sh done AG-DYNUI-PROD-003 "..."`.

Until step 1 completes, steps 2-4 cannot produce meaningful hosted proof of
this specific feature, even though the BFF and dev host are otherwise healthy.

---

## 5. Parent / Reviewer Checklist

For `Claude2` (parent reviewer) and `Codex` (parent owner) to confirm before
`AG-DYNUI-PROD-003` moves to `done`:

- [ ] A PR exists and is merged on `ajoe734/execute-plans` carrying the
  `TradingRoomDefaultEntry` / `selectDefaultReadyStrategy` change (or an
  equivalent), not just on this pantheon repo's in-tree mirror.
- [ ] A human-approved `workflow_dispatch` deploy ran against `dev` after that
  merge, and `deployment.json` reflects a matching commit.
- [ ] Hosted screenshots exist for all three default-entry states named in
  §4.4, captured against the redeployed host, not against the in-tree mirror
  or local dev server.
- [ ] The `onAddToTradingRoom` gap is explicitly filed against
  `AG-DYNUI-PROD-005` (or an equivalent follow-up) rather than silently
  dropped, since it remains unwired in both repos.

---

## 6. Parent Boundary Notes

Unchanged from the original packet — restated for this follow-up's own
traceability:

Owned by `AG-DYNUI-PROD-003` parent (already implemented in `eab6e0cfd` on
this repo's in-tree mirror):

- default-entry branching logic in `TradingRoomPage.tsx`;
- `selectDefaultReadyStrategy()` and `TradingRoomDefaultEntry` component;
- `onOpenWorkshop` wiring in `agora-main.tsx`.

Not owned by this sidecar, and now confirmed still outstanding:

- porting/merging that diff into the standalone `ajoe734/execute-plans` repo;
- requesting and completing the human-gated dev deploy dispatch;
- capturing hosted screenshot evidence;
- wiring `onAddToTradingRoom` from Workshop back into Trading Room
  (candidate: `AG-DYNUI-PROD-005`);
- BFF route/schema/registry/governance runtime changes — still none required
  for this feature.

---

## 7. Verification Performed For This Sidecar

```bash
git status --short
git branch --show-current
AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-2
AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-003
gh pr view 2860 --repo ajoe734/pantheon --json number,title,url,mergeCommit,headRefName,state,mergedAt
gh pr view 2860 --repo ajoe734/pantheon --json files --jq '.files[].path'
gh pr list --repo ajoe734/execute-plans --search "trading-room" --state all --json number,title,state,headRefName,url
gh pr list --repo ajoe734/execute-plans --state open --json number,title,state,headRefName,url
git -C /home/lupin/code/execute-plans fetch origin dev
git -C /home/lupin/code/execute-plans log --oneline -5 origin/dev
git -C /home/lupin/code/execute-plans log --oneline -5 origin/dev -- src/agora/pages/trading-room/TradingRoomPage.tsx
git -C /home/lupin/code/execute-plans show origin/dev:src/agora/pages/trading-room/TradingRoomPage.tsx | grep -n "TradingRoomDefaultEntry\|selectDefaultReadyStrategy"
git -C /home/lupin/code/execute-plans show origin/dev:src/entries/agora-main.tsx | grep -n "onAddToTradingRoom\|onOpenWorkshop"
grep -n "onAddToTradingRoom\|onOpenWorkshop" execute-plans/src/entries/agora-main.tsx
curl -sS --max-time 10 https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json
curl -sS --max-time 10 https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health
sed -n '1,70p' .github/workflows/nonprod-deploy.yml
AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-004
gh run view 28689452900 --repo ajoe734/pantheon --json displayTitle,conclusion,createdAt,event,workflowName
```

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned, per the task-scoped read-order instruction. No runtime, canonical,
registry, governance, frontend, or hosted-environment change was made by this
sidecar — verification was read-only inspection plus anonymous/health-only
HTTP probes.

---

## 8. Reviewer Handoff

Reviewer (`Claude2`) should verify:

1. This packet is support-only and does not mutate canonical truth, runtime
   code, frontend code, route registry, or governance behavior.
2. §2's claim that the real `ajoe734/execute-plans` repo's `dev` branch still
   lacks the PROD-003 feature is accurate (re-run the `grep` against
   `origin/dev` if the standalone repo has moved since `2026-07-04T01:2x`).
3. The recommended sequence in §4 (port -> merge -> human-gated deploy
   dispatch -> hosted screenshots) correctly orders the remaining closeout
   work for parent owner `Codex`, rather than skipping straight to a deploy
   dispatch that would not actually carry the fix.
4. Parent (`Codex`) can use this packet to plan the remaining closeout without
   treating it as review approval for `AG-DYNUI-PROD-003` itself — that
   approval was already recorded independently in `ai-status.json`.

No further sidecar-owned work is expected from `Claude` unless the reviewer
finds this handoff inaccurate or the publish state changes materially.
