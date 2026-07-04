# AG-DYNUI-PROD-002 Sidecar BFF Handoff Follow-up 3

| Field | Value |
|---|---|
| Task ID | `AG-DYNUI-PROD-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` |
| Parent task | `AG-DYNUI-PROD-002` (Agora standalone workbench shell) |
| Parent owner / reviewer | `Claude` / `Claude2` |
| Sidecar owner / reviewer | `Claude2` / `Claude` |
| Helper kind | `bff_handoff_packet` |
| Prepared | 2026-07-04 |
| Mutates canonical | `false` |

This is a support-only follow-up to `AG-DYNUI-PROD-002-SIDECAR-BFF-HANDOFF.md`
and `AG-DYNUI-PROD-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md`. It does not change
canonical architecture, L1 contract truth, BFF runtime behavior, route
registries, frontend implementation, deploy configuration, governance policy,
or task state by hand.

Since Follow-up 2, the parent has not moved: it is still `review_approved`
and its own progress notes ("check 45") already independently re-confirmed
the same blocker described below just before this packet was prepared. This
follow-up's job is to re-verify that read from a clean, independent check,
confirm no state has drifted since Follow-up 2, and make explicit what is
*not yet happening* that would actually unblock the parent — nobody currently
owns the one task that can produce the missing closeout evidence.

---

## 1. Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates work; support packets do not override L1/L2 architecture or task ownership. |
| `.orchestrator/task-briefs/ag_dynui_prod_002_sidecar_bff_handoff_followup_3.md` | Sidecar was auto-reassigned to `Claude2` after `Copilot` hit a monthly-quota terminal on an earlier attempt; scope is unchanged (BFF query gap, operator journey, frontend handoff material only). |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` (against `PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon`, not the worktree mirror) | Live state is `in_progress`, owner `Claude2`, reviewer `Claude`, artifact path is this file. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-002` | Parent is still `review_approved`, owner `Claude`, reviewer `Claude2`, `last_update: 2026-07-04T05:39:45Z`. Its own `next` note ("check 45") already states: PR #171 (head `67c0b0480d0999a2b8318c3d9ad44366f5b2f768`, unchanged) still `OPEN`/`MERGEABLE`/`CLEAN`, `integration-gate SUCCESS`, zero reviews, `autoMergeRequest: null`, governance-blocked on human self-merge; `AG-DYNUI-PROD-006` confirmed still `todo`/unowned. |
| `gh pr view 171 --repo ajoe734/execute-plans --json ...` (independent re-check, not trusting the parent's cached note) | Matches the parent's note exactly: `state: OPEN`, `mergeCommit: null`, `mergeStateStatus: CLEAN`, `mergeable: MERGEABLE`, `reviewDecision: ""`, `autoMergeRequest: null`, `integration-gate` check `SUCCESS`. Head SHA unchanged from Follow-up 2. |
| `gh pr view 170 --repo ajoe734/execute-plans --json ...` | Still `CLOSED`, `mergedAt: null` — unchanged. |
| `git ls-remote https://github.com/ajoe734/execute-plans.git dev` | `dev` tip is still `dd597405e014cc91cf73f4ea2e96a561fcbf9c61` — byte-identical to the tip Follow-up 2 recorded. The shell fix has still not merged. |
| Pantheon dev FE `/deployment.json` | Still reports `commit: dd597405e014cc91cf73f4ea2e96a561fcbf9c61`, `sourceBranch: dev`, `VITE_BFF_MODE: live`, `VITE_BFF_FALLBACK: strict`, `VITE_BFF_REAL_WRITES: false` — same pre-fix commit as Follow-up 2 observed; no redeploy has happened. |
| Dev BFF `/health` | `{"status":"ok","service":"operator-bff","version":"0.2.0"}` — service reachable, version unchanged from prior packets. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show` for `AG-DYNUI-PROD-003`/`004`/`005`/`006` | `003` is `review_approved`, owner now `Claude` (was `Codex2` at Follow-up 2 time — an ownership change on a fleet sibling, noted for completeness, out of this packet's scope). `004` is fully archived (`ai-task-archive/tasks/AG-DYNUI-PROD-004.json`, `terminal_outcome: completed`) — matches Follow-up 2's note that its diagnostics fix is mirror-only and not live on `ajoe734/execute-plans`. `005` is `todo`, owner `Claude`, reviewer `Codex2` — unchanged. `006` (hosted E2E/publish gate, the task actually blocked on this packet's facts) is still `todo`, owner `Codex`, reviewer `Claude2`, with `next: "Assignment created from Agora DYNUI production-gap packet."` — i.e. **no one has started work on it since Follow-up 2**. |
| `.github/workflows/nonprod-deploy.yml` | Unchanged: `push` to `publish/v*` redeploys dev, `push` to `master` redeploys staging-live, `workflow_dispatch` is human-authorized. A plain merge to `execute-plans` `dev` still would not itself trigger a hosted redeploy. |
| `[[project_agora_pr_self_merge_governance_block]]` (memory, reconfirmed again) | Still holds: PR #171's state (approved-equivalent review, CI green, clean/mergeable, unmerged) is the known AI self-merge governance block, not a review or CI gap. Neither this packet nor the parent should attempt `gh pr merge` again. |

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned.

---

## 2. Delta Since Follow-up 2

| Area | Follow-up 2 (prior read) | This follow-up (current read) |
|---|---|---|
| `execute-plans` PR #171 | `OPEN`, `CLEAN`, `MERGEABLE`, `integration-gate SUCCESS`, unmerged, governance-blocked. | **Unchanged.** Same head SHA, same check state, still `OPEN`/unmerged. |
| `execute-plans` PR #170 | `CLOSED`, unmerged, superseded by #171. | **Unchanged.** |
| `execute-plans` `dev` tip | `dd597405e0` (PR #172 merge, unrelated persona-fleet UI task); shell fix not present. | **Unchanged** — identical tip SHA. Shell fix still not present on `dev`. |
| Hosted dev FE `/deployment.json` | `commit: dd597405e0...`, same pre-fix code. | **Unchanged** — identical commit, identical deploy timestamp (`20260704T012041Z`). No redeploy has occurred. |
| Parent (`AG-DYNUI-PROD-002`) status | `review_approved`, owner `Claude`, reviewer `Claude2`; owner must not run `done` until closeout evidence is available. | **Unchanged status**, but the owner's own progress log now shows repeated re-checks ("check 44", "check 45") confirming the same blocker rather than new progress — the parent is correctly not attempting to force a merge, but is also not able to advance without a human or without `AG-DYNUI-PROD-006`. |
| `AG-DYNUI-PROD-006` (the task that can supply the missing hosted-screenshot evidence) | `todo`, owner `Codex`, reviewer `Claude2`, unowned/unstarted. | **Still `todo`, still unstarted.** This is the concrete gap this follow-up flags: the parent cannot close, and the downstream task that would unblock it has had no owner activity since at least Follow-up 2. |
| `AG-DYNUI-PROD-003` (fleet sibling, not this task's scope) | `review_approved`, owner `Codex2`. | Owner changed to `Claude` (still `review_approved`). Noted only because it shares the same fleet lane; not investigated further as it is outside this sidecar's scope. |

**Net finding: nothing has changed on the ground since Follow-up 2.** The
blocker is exactly what Follow-up 2 described — a human merge decision on PR
#171, then a deploy trigger, then hosted screenshot capture — and none of
those three steps has happened. The one new fact this follow-up adds is that
`AG-DYNUI-PROD-006`, the task whose scope is precisely "capture that hosted
proof," remains unowned in practice (assigned but with zero progress notes),
so the parent's blocker will not resolve itself without either a human merge
action on PR #171 or an owner picking up `AG-DYNUI-PROD-006`.

No sidecar-owned code change is made here.

---

## 3. BFF Query Surface And Gap Matrix (Unchanged)

Still no new BFF contract need identified. Confirmed again against the live
dev BFF this cycle:

- `GET /health` → `{"status":"ok","service":"operator-bff","version":"0.2.0"}`.
- Trading Room aggregate/decision-events reads
  (`GET /bff/agora/trading-room`, `/decision-events`).
- Trading Room proposal/workspace/widget-revision/version/rollback writes
  remain observation- and request-only (no broker order routing, no capital
  binding, no `RuntimeBinding` mutation).
- Workshop list/create/get/messages/events/completeness/stream reads; version,
  research-run, consultation, and conclude routes remain `501` stubs.

Route composition changes owned by the parent must continue to go through
these existing BFF client modules and must not add ad hoc fetches or bypass
strict/live BFF env settings.

---

## 4. Operator Journey Update (Reconfirmed, Unchanged Guidance)

The sequence for whoever actually picks up hosted verification next remains
exactly as Follow-up 2 stated, and is reconfirmed still accurate:

1. Confirm `ajoe734/execute-plans` PR #171 merge state with
   `gh pr view 171 --repo ajoe734/execute-plans --json state,mergeCommit`
   before trusting any hosted probe. It is still `OPEN` as of this packet.
   This is a **human merge decision**, not something the owner/reviewer can
   push through — see `[[project_agora_pr_self_merge_governance_block]]`. Do
   not attempt `gh pr merge` again; it has already been correctly avoided
   twice.
2. After PR #171 merges, re-check `execute-plans` `dev` tip and confirm the
   hosted dev FE `/deployment.json` `commit` field is a descendant of the
   merge commit (`git merge-base --is-ancestor <merge-sha> <deployed-commit>`).
   A plain merge does **not** trigger redeploy; `nonprod-deploy.yml` only
   fires on `publish/v*` push, `master` push, or a human-authorized
   `workflow_dispatch`. Expect a stale bundle until one of those happens.
3. Only once the deployed commit descends from the PR #171 merge, capture
   desktop and mobile screenshots of `/agora/trading-room` and
   `/agora/strategy-workshop/:workshopId`, and confirm:
   - no Management `TopBar`/`NotificationCenter`/drawers render inside
     `/agora/*`;
   - `LiveStatusBanner` (or an Agora-equivalent live-status surface) is still
     visible;
   - the servant drawer shows real workshop context (subject/status/message
     count) or an explicit degraded/error state, not the old static
     placeholder text;
   - narrow-viewport layout does not clip the tab bar or fix the drawer at a
     non-responsive width.
4. Record the verified merge SHA, deploy run id/timestamp, and screenshot
   evidence in the `AG-DYNUI-PROD-006` artifact — do not reuse the parent's
   source-only or local-dev-server verification notes as hosted proof.
5. **New in this follow-up:** if `AG-DYNUI-PROD-006` remains unowned/unstarted
   for another dispatch cycle after this packet, that is itself worth a
   blocker note on `AG-DYNUI-PROD-006` (not on the parent) pointing at PR
   #171's open state, so supervisor dispatch can prioritize either a human
   merge nudge or an owner claim, instead of the fleet quietly waiting.

---

## 5. Ownership Boundaries

Owned here:

- restating the post-Follow-up-2 delta (or lack of one) with an independent
  re-check, not a copy of the prior packet's numbers;
- confirming (read-only) that PR #171, `execute-plans` `dev`, and the hosted
  dev FE are all still in the exact pre-merge state Follow-up 2 described;
- flagging that `AG-DYNUI-PROD-006` — the task that can actually produce the
  parent's missing closeout evidence — has had no owner progress since at
  least Follow-up 2.

Not owned here:

- merging or attempting to merge PR #170/#171;
- dispatching `nonprod-deploy.yml` or any other deploy action;
- editing `execute-plans` (vendored mirror or the real repo), BFF routes,
  schemas, registries, or governance code;
- changing `AG-DYNUI-PROD-002`'s or `AG-DYNUI-PROD-006`'s status; only their
  respective owners may finalize them.

---

## 6. Reviewer Handoff

Reviewer (`Claude`) should verify:

1. This packet is a support artifact only and does not introduce canonical
   contract truth or attempt any merge/deploy action.
2. PR #170/#171 state and the `execute-plans` `dev` tip are read correctly
   and match `gh pr view` / `git ls-remote` at review time — re-check, since
   state can change between packet authoring and review.
3. The claim that the hosted dev FE has not deployed the fix is grounded in
   the `/deployment.json` commit not descending from PR #171's head, not
   merely in "state looks stale."
4. The `AG-DYNUI-PROD-006` ownership/progress observation is accurate at
   review time (re-run `python3 scripts/ai_status.py show AG-DYNUI-PROD-006`
   against `PANTHEON_STATUS_ROOT`, not the worktree mirror).
5. The packet does not imply the parent, this sidecar, or `AG-DYNUI-PROD-006`
   should force a merge or manual deploy; it should route that decision to a
   human per `[[project_agora_pr_self_merge_governance_block]]`.

---

## 7. Verification Notes

Verification was source inspection, `gh`/`git ls-remote` read probing of
`ajoe734/execute-plans`, live `ai_status.py show` reads against
`PANTHEON_STATUS_ROOT`, and anonymous hosted read probing only. No runtime,
frontend, canonical, registry, governance, deploy, merge, or hosted
environment changes were made.

Commands used:

```bash
git branch --show-current
git status --short
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-3
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-002
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-003
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-004
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-005
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-006
gh pr view 170 --repo ajoe734/execute-plans --json number,state,mergedAt,mergeCommit,headRefName,baseRefName,url
gh pr view 171 --repo ajoe734/execute-plans --json number,state,mergedAt,mergeCommit,headRefName,baseRefName,url,statusCheckRollup,mergeable,mergeStateStatus,reviewDecision,autoMergeRequest
git ls-remote https://github.com/ajoe734/execute-plans.git dev
curl -sS https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json
curl -sS https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health
sed -n '1,40p' .github/workflows/nonprod-deploy.yml
cat /home/lupin/code/pantheon/ai-task-archive/tasks/AG-DYNUI-PROD-004.json
```
