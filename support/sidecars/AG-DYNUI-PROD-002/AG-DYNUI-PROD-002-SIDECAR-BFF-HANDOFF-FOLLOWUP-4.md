# AG-DYNUI-PROD-002 Sidecar BFF Handoff Follow-up 4

| Field | Value |
|---|---|
| Task ID | `AG-DYNUI-PROD-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` |
| Parent task | `AG-DYNUI-PROD-002` (Agora standalone workbench shell) |
| Parent owner / reviewer | `Claude` / `Claude2` |
| Sidecar owner / reviewer | `Claude2` / `Claude` |
| Helper kind | `bff_handoff_packet` |
| Prepared | 2026-07-04 |
| Mutates canonical | `false` |

This is a support-only follow-up to `AG-DYNUI-PROD-002-SIDECAR-BFF-HANDOFF.md`,
`...-FOLLOWUP-2.md`, and `...-FOLLOWUP-3.md`. It does not change canonical
architecture, L1 contract truth, BFF runtime behavior, route registries,
frontend implementation, deploy configuration, governance policy, or task
state by hand.

Since Follow-up 3, the parent has not moved: it is still `review_approved`
and its own progress notes ("check 45") already independently re-confirmed
the same blocker described below. This follow-up re-verifies that read from
an independent check, confirms nothing has drifted since Follow-up 3, and
records that this is now the fourth consecutive sidecar cycle in which the
one blocking fact set (PR #171 unmerged, hosted FE undeployed,
`AG-DYNUI-PROD-006` unowned) has not changed at all.

---

## 1. Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates work; support packets do not override L1/L2 architecture or task ownership. |
| `.orchestrator/task-briefs/ag_dynui_prod_002_sidecar_bff_handoff_followup_4.md` | Sidecar was auto-reassigned to `Claude2` after `Copilot` hit a monthly-quota terminal on an earlier attempt; scope is unchanged (BFF query gap, operator journey, frontend handoff material only). |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` (against `PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon`, not the worktree mirror) | Live state is `in_progress` (supervisor auto-started it after dispatch), owner `Claude2`, reviewer `Claude`, artifact path is this file. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-DYNUI-PROD-002` | Parent is still `review_approved`, owner `Claude`, reviewer `Claude2`, `last_update: 2026-07-04T05:39:45Z` — unchanged since Follow-up 3. Its own `next` note ("check 45") states PR #171 (head `67c0b0480d0999a2b8318c3d9ad44366f5b2f768`, unchanged) is still `OPEN`/`MERGEABLE`/`CLEAN`, `integration-gate SUCCESS`, zero reviews, `autoMergeRequest: null`, governance-blocked on human self-merge; it also records that a `ToolSearch` for an `orchestrator_approval_broker` self-merge-approval tool again found nothing. |
| `gh pr view 171 --repo ajoe734/execute-plans --json ...` (independent re-check) | Matches the parent's note exactly: `state: OPEN`, `mergeCommit: null`, `mergeStateStatus: CLEAN`, `mergeable: MERGEABLE`, `reviewDecision: ""`, `autoMergeRequest: null`, `integration-gate` check `SUCCESS`. Head SHA unchanged from Follow-up 2/3. |
| `gh pr view 170 --repo ajoe734/execute-plans --json ...` | Still `CLOSED`, `mergedAt: null` — unchanged. |
| `git ls-remote https://github.com/ajoe734/execute-plans.git dev` | `dev` tip is still `dd597405e014cc91cf73f4ea2e96a561fcbf9c61` — byte-identical to the tip Follow-up 2 and Follow-up 3 recorded. The shell fix has still not merged. |
| Pantheon dev FE `/deployment.json` | Still reports `commit: dd597405e014cc91cf73f4ea2e96a561fcbf9c61`, `deployedAt: 20260704T012041Z`, `sourceBranch: dev`, `VITE_BFF_MODE: live`, `VITE_BFF_FALLBACK: strict`, `VITE_BFF_REAL_WRITES: false` — identical commit and deploy timestamp to Follow-up 3; no redeploy has happened. |
| Dev BFF `/health` | `{"status":"ok","service":"operator-bff","version":"0.2.0"}` — service reachable, version unchanged. |
| `ToolSearch "orchestrator_approval_broker self-merge approve execute-plans PR"` (independent re-check) | Returned `Monitor` and `WebFetch` only — no `orchestrator_approval_broker` tool or self-merge-approval capability is exposed to this session either. Matches the parent's own note; the self-merge governance block remains a human-only path. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show` for `AG-DYNUI-PROD-003`/`005`/`006` | `003` is now `review_approved`, owner `Claude` (was `Codex2` at Follow-up 2, then reassigned before Follow-up 3; supervisor just resumed it "for finalize" — reviewer notes there record an approved PR #2860 but flag that live no-strategy/ready-strategy screenshot evidence is still outstanding, mirroring the same "PR approved, hosted proof missing" pattern as this task). `005` is still `todo`, owner `Claude`, reviewer `Codex2` — unchanged. `006` (hosted E2E/publish gate, the task actually blocked on this packet's facts) is still `todo`, owner `Codex`, reviewer `Claude2`, `next: "Assignment created from Agora DYNUI production-gap packet."` — **no owner progress recorded across Follow-up 2, 3, or this cycle.** |
| `.github/workflows/nonprod-deploy.yml` | Not re-read this cycle; Follow-up 2/3 already confirmed the trigger set (`publish/v*` push, `master` push, human `workflow_dispatch`) and nothing in this cycle's evidence suggests it changed. |
| `[[project_agora_pr_self_merge_governance_block]]` (memory, reconfirmed a third time) | Still holds: PR #171's state (approved-equivalent review, CI green, clean/mergeable, unmerged) is the known AI self-merge governance block, not a review or CI gap. This packet does not attempt `gh pr merge`. |

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned.

---

## 2. Delta Since Follow-up 3

| Area | Follow-up 3 (prior read) | This follow-up (current read) |
|---|---|---|
| `execute-plans` PR #171 | `OPEN`, `CLEAN`, `MERGEABLE`, `integration-gate SUCCESS`, unmerged, governance-blocked. | **Unchanged.** Same head SHA, same check state, still `OPEN`/unmerged. |
| `execute-plans` PR #170 | `CLOSED`, unmerged, superseded by #171. | **Unchanged.** |
| `execute-plans` `dev` tip | `dd597405e0` (PR #172 merge, unrelated persona-fleet UI task); shell fix not present. | **Unchanged** — identical tip SHA. |
| Hosted dev FE `/deployment.json` | `commit: dd597405e0...`, `deployedAt: 20260704T012041Z`. | **Unchanged** — identical commit and deploy timestamp. No redeploy has occurred across three follow-up cycles. |
| Parent (`AG-DYNUI-PROD-002`) status | `review_approved`; owner correctly not forcing a merge, but blocked without a human action or `AG-DYNUI-PROD-006`. | **Unchanged status.** Owner's own log now shows "check 45" as a pure re-confirmation, plus a new negative-result check (`ToolSearch` for a self-merge approval tool) — consistent with the owner having exhausted the actions available to it short of a human decision. |
| `AG-DYNUI-PROD-006` | `todo`, owner `Codex`, unowned/unstarted since at least Follow-up 2. | **Still `todo`, still unstarted.** This is now three sidecar cycles (Follow-up 2, 3, and this one) with zero progress on the one task whose scope is exactly "produce the missing hosted-screenshot evidence." |
| `AG-DYNUI-PROD-003` (fleet sibling, not this task's scope) | `review_approved`, owner `Claude` (changed from `Codex2` at Follow-up 2). | Still `review_approved`, same owner; supervisor just resumed it for finalize. Its reviewer notes independently show the identical shape of blocker: PR approved, but live screenshot evidence still outstanding before `done`. Noted only for pattern-recognition value across the fleet lane; not investigated further as it is outside this sidecar's scope. |

**Net finding: nothing has changed on the ground since Follow-up 3.** The
blocker is exactly what Follow-up 2 first described and Follow-up 3
reconfirmed — a human merge decision on PR #171, then a deploy trigger, then
hosted screenshot capture — and none of those three steps has happened. The
new observation this follow-up adds is that the same "approved-but-no-hosted-proof"
shape is now visible on a second task in the same fleet lane
(`AG-DYNUI-PROD-003`), which suggests the missing piece is not specific to
this task's PR but a structural gap in how this fleet lane reaches hosted
deploy/screenshot evidence at all.

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
  research-run, consultation, and conclude routes remain `501` stubs (not
  re-probed this cycle; no signal suggests they changed).

Route composition changes owned by the parent must continue to go through
these existing BFF client modules and must not add ad hoc fetches or bypass
strict/live BFF env settings.

---

## 4. Operator Journey Update (Reconfirmed, Unchanged Guidance)

The sequence for whoever actually picks up hosted verification next remains
exactly as Follow-up 2/3 stated, and is reconfirmed still accurate:

1. Confirm `ajoe734/execute-plans` PR #171 merge state with
   `gh pr view 171 --repo ajoe734/execute-plans --json state,mergeCommit`
   before trusting any hosted probe. It is still `OPEN` as of this packet.
   This is a **human merge decision**, not something the owner/reviewer can
   push through — see `[[project_agora_pr_self_merge_governance_block]]`. Do
   not attempt `gh pr merge`; it has already been correctly avoided across
   three prior cycles, and no approval-broker tool is available to this
   session to route it automatically either.
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
5. **New in this follow-up:** the same missing-hosted-proof shape now appears
   on `AG-DYNUI-PROD-003` as well (PR #2860 approved, live screenshots still
   outstanding). Whoever owns unblocking this fleet lane should treat
   "get one human-authorized `nonprod-deploy.yml` dispatch (or PR #171 merge)
   done" as a shared unblock for at least two tasks, not a one-off for this
   task alone.

---

## 5. Ownership Boundaries

Owned here:

- restating the post-Follow-up-3 delta (or lack of one) with an independent
  re-check, not a copy of the prior packet's numbers;
- confirming (read-only) that PR #171, `execute-plans` `dev`, and the hosted
  dev FE are all still in the exact pre-merge state Follow-up 2/3 described;
- flagging that `AG-DYNUI-PROD-006` has now had zero owner progress across
  three consecutive sidecar cycles, and that the same blocker shape has
  spread to `AG-DYNUI-PROD-003`.

Not owned here:

- merging or attempting to merge PR #170/#171;
- dispatching `nonprod-deploy.yml` or any other deploy action;
- editing `execute-plans` (vendored mirror or the real repo), BFF routes,
  schemas, registries, or governance code;
- changing `AG-DYNUI-PROD-002`'s, `AG-DYNUI-PROD-003`'s, or
  `AG-DYNUI-PROD-006`'s status; only their respective owners may finalize
  them. This sidecar does not open a blocker on `AG-DYNUI-PROD-006` itself —
  its acceptance criteria restrict it to producing support artifacts only.

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
4. The `AG-DYNUI-PROD-006`/`AG-DYNUI-PROD-003` observations are accurate at
   review time (re-run `python3 scripts/ai_status.py show <task-id>` against
   `PANTHEON_STATUS_ROOT`, not the worktree mirror).
5. The packet does not imply the parent, this sidecar, or `AG-DYNUI-PROD-006`
   should force a merge or manual deploy; it should route that decision to a
   human per `[[project_agora_pr_self_merge_governance_block]]`.

---

## 7. Verification Notes

Verification was source inspection, `gh`/`git ls-remote` read probing of
`ajoe734/execute-plans`, live `ai_status.py show` reads against
`PANTHEON_STATUS_ROOT`, a `ToolSearch` probe for a self-merge-approval tool,
and anonymous hosted read probing only. No runtime, frontend, canonical,
registry, governance, deploy, merge, or hosted environment changes were made.

Commands used:

```bash
git branch --show-current
git status
AI_NAME=Claude2 PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/ai_status.py show AG-DYNUI-PROD-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4
AI_NAME=Claude2 PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/ai_status.py show AG-DYNUI-PROD-002
AI_NAME=Claude2 PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/ai_status.py show AG-DYNUI-PROD-003
AI_NAME=Claude2 PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/ai_status.py show AG-DYNUI-PROD-005
AI_NAME=Claude2 PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/ai_status.py show AG-DYNUI-PROD-006
gh pr view 170 --repo ajoe734/execute-plans --json number,state,mergedAt,mergeCommit,headRefName,baseRefName,url
gh pr view 171 --repo ajoe734/execute-plans --json number,state,mergedAt,mergeCommit,headRefName,baseRefName,url,statusCheckRollup,mergeable,mergeStateStatus,reviewDecision,autoMergeRequest
git ls-remote https://github.com/ajoe734/execute-plans.git dev
curl -sS https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json
curl -sS https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health
```

ToolSearch probe (not a shell command): `"orchestrator_approval_broker
self-merge approve execute-plans PR"` — returned only `Monitor` and
`WebFetch`; no self-merge-approval capability is exposed to this session.
