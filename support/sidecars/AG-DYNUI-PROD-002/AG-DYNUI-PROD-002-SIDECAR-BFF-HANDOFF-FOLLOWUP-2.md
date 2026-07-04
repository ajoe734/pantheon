# AG-DYNUI-PROD-002 Sidecar BFF Handoff Follow-up 2

| Field | Value |
|---|---|
| Task ID | `AG-DYNUI-PROD-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Parent task | `AG-DYNUI-PROD-002` (Agora standalone workbench shell) |
| Parent owner / reviewer | `Claude` / `Claude2` |
| Sidecar owner / reviewer | `Claude` / `Codex2` |
| Helper kind | `bff_handoff_packet` |
| Prepared | 2026-07-04 |
| Mutates canonical | `false` |

This is a support-only follow-up to
`AG-DYNUI-PROD-002-SIDECAR-BFF-HANDOFF.md`. It does not change canonical
architecture, L1 contract truth, BFF runtime behavior, route registries,
frontend implementation, deploy configuration, governance policy, or task
state by hand.

Since the original packet, the parent has moved to `review_approved` and
recorded an implementation (`ajoe734/execute-plans` PR #170, superseded by
PR #171). This follow-up's job is to verify what is actually live for
downstream `AG-DYNUI-PROD-006` (hosted E2E/publish gate) before that task, or
the parent's own `done` transition, treats the shell fix as deployed.

---

## 1. Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates work; support packets do not override L1/L2 architecture or task ownership. |
| `.orchestrator/task-briefs/ag_dynui_prod_002_sidecar_bff_handoff_followup_2.md` | Sidecar was auto-reassigned to `Claude` after `Codex` hit a usage-limit terminal on an earlier attempt; scope is unchanged (BFF query gap, operator journey, frontend handoff material only). |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | Live state (not the worktree mirror) is `in_progress`, owner `Claude`, reviewer `Codex2`, artifact path is this file. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-002` | Parent is `review_approved`, owner `Claude`, reviewer `Claude2`. Reviewer notes: independently re-ran `ajoe734/execute-plans` PR #171 (commit `67c0b048`) — 118 files / 1102 tests, `tsc --noEmit`, build, eslint all pass; approved but the owner **may not** run `ai-status.sh done` until `AG-DYNUI-PROD-006` supplies hosted desktop/mobile screenshots. |
| `docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-002-standalone-workbench-shell.md` | Owner's implementation notes (2026-07-04): the pantheon-vendored `execute-plans/` mirror is **not** canonical; the real fix landed only in `ajoe734/execute-plans` PR #170 (closed, superseded by #171). Notes explicitly defer hosted screenshot proof to `AG-DYNUI-PROD-006` and flag that `AG-DYNUI-PROD-004`'s reviewer-approved diagnostics changes are still mirror-only and not live on the hosted dev FE. |
| `gh pr view 170/171 --repo ajoe734/execute-plans` | PR #170 is `CLOSED` (not merged, unmerged head `task/AG-DYNUI-PROD-002-agora-standalone-shell`). PR #171 (`task/AG-DYNUI-PROD-002-agora-standalone-shell-compliant` → `dev`) is `OPEN`, `mergeStateStatus: CLEAN`, `mergeable: MERGEABLE`, `integration-gate: SUCCESS`, still unmerged as of this check. |
| `git clone --branch dev https://github.com/ajoe734/execute-plans.git` (fresh clone, not the dirty local checkout) | `dev` tip is `dd597405e0` (merge of PR #172, an unrelated persona-fleet UI task), **not** a descendant of PR #171's `67c0b048`. `src/App.tsx` still nests `<Route path="/agora" ...>` inside `<Route element={<PlatformShellRoute />}>`; `src/routes/agora.tsx`'s `AgoraLayoutRoute` still renders bare `<TradingDeskLayout />` with no `LiveStatusBanner` extraction, and `AgoraStrategyWorkshopRoute` still does not read `:workshopId`; `TradingDeskLayout.tsx`'s `ServantDrawer` still shows the static "Servant panel — workshop context loads here." placeholder. The shell fix is **not yet present on `dev`**. |
| Pantheon dev FE `/deployment.json` | Hosted dev FE reports `commit: dd597405e014cc91cf73f4ea2e96a561fcbf9c61`, i.e. the same pre-fix `dev` tip above, `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`, `VITE_BFF_REAL_WRITES=false`. The hosted FE has not deployed PR #171 because PR #171 has not merged. |
| `[[project_agora_pr_self_merge_governance_block]]` (prior-session memory, reconfirmed here) | PR #171's exact state — reviewer-approved-equivalent, `CLEAN`/`MERGEABLE`/CI green, still unmerged — matches a standing, previously observed governance block: neither the AI owner nor reviewer can self-merge a task PR into `ajoe734/execute-plans` `dev`; the harness's auto-mode classifier blocks `gh pr merge` as a self-merge-without-human-approval risk regardless of review/CI state. This is very likely why PR #171 is still open, not a review or CI gap. |
| `.github/workflows/nonprod-deploy.yml` | Confirms current trigger set: `push` to `publish/v*` (nightly cut) redeploys **dev**; `push` to `master` redeploys **staging-live**; `workflow_dispatch` (human-authorized) can deploy either. A plain merge to `execute-plans` `dev` does **not** itself redeploy the hosted dev FE — deploy is a separate, human-gated step even after PR #171 lands. |
| `AI_NAME=Claude python3 scripts/ai_status.py show` for `AG-DYNUI-PROD-003`/`005`/`006` | `003` is `review_approved` (owner `Codex2`, reviewer `Claude2`). `005` is `todo` (owner `Claude`, reviewer `Codex2`). `006` (hosted E2E/publish gate) is `todo` (owner `Codex`, reviewer `Claude2`) — this is the task blocked on the facts in this packet. `004` is archived `done`, but per the parent's own note its diagnostics fix is mirror-only and not on the hosted dev FE either. |

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned.

---

## 2. Delta Since The Original Handoff

| Area | Original packet (2026-07-04, pre-implementation) | Current follow-up read |
|---|---|---|
| Route composition | `/agora` nested under `PlatformShellRoute`; described as the open gap to fix. | Parent implemented the fix in `ajoe734/execute-plans` PR #171 (source-only), but the fix has **not merged** to `execute-plans` `dev` and the hosted dev FE still runs the pre-fix code. The route-composition gap described in the original packet is still live on the hosted environment today. |
| Workshop deep link / servant context | `AgoraStrategyWorkshopRoute` does not read `:workshopId`; `TradingDeskLayout` never receives it. | Same as before on hosted `dev` — unchanged, because the fix that addresses this (also in PR #171) has not merged either. |
| Parent branch/PR | "No remote `task/AG-DYNUI-PROD-002*` branch was found." | Two PRs now exist: `#170` (closed, unmerged) and `#171` (open, clean, mergeable, CI green, unmerged). Reviewer already re-verified PR #171's diff and tests independently. |
| Closeout gate | Not yet reached. | Parent is `review_approved` with an explicit reviewer condition: owner must not call `done` before `AG-DYNUI-PROD-006` supplies hosted screenshots. This follow-up packet shows that gate cannot be satisfied yet — PR #171 must merge, and the dev FE must be redeployed, before any hosted screenshot can reflect the fix. |
| AG-DYNUI-PROD-004 residual gap | Not covered (out of original packet's scope). | Parent's own implementation notes flag that `AG-DYNUI-PROD-004`'s approved diagnostics changes are also mirror-only and not on `ajoe734/execute-plans`. `AG-DYNUI-PROD-006` should not assume that task's proof is live either. |

No sidecar-owned code change is made here.

---

## 3. BFF Query Surface And Gap Matrix (Unchanged)

This follow-up found no new BFF contract need. The relevant surfaces are the
same ones the original packet identified and remain accurate on the current
hosted dev BFF (`health` reports `operator-bff 0.2.0`; unauthenticated
`GET /bff/agora/me` still fails closed with `AUTH_REQUIRED`):

- Trading Room aggregate/decision-events reads (`GET /bff/agora/trading-room`,
  `/decision-events`).
- Trading Room proposal/workspace/widget-revision/version/rollback writes,
  observation- and request-only (no broker order routing, no capital binding,
  no `RuntimeBinding` mutation).
- Workshop list/create/get/messages/events/completeness/stream reads; version,
  research-run, consultation, and conclude routes remain `501` stubs.

Route composition changes owned by the parent must not add ad hoc fetches
around these BFF client modules, and must not reroute around strict/live BFF
env settings.

---

## 4. Operator Journey Update

Use this sequence for whoever picks up hosted verification next
(`AG-DYNUI-PROD-006` or the parent's own closeout):

1. Confirm `ajoe734/execute-plans` PR #171 merge state with
   `gh pr view 171 --repo ajoe734/execute-plans --json state,mergeCommit`
   before trusting any hosted probe. If still `OPEN`, this is a **human
   merge decision**, not a task the owner/reviewer can push through — see
   `[[project_agora_pr_self_merge_governance_block]]`. Do not retry
   `gh pr merge` a third time if it has already been blocked twice.
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
   source-only verification notes as hosted proof.

---

## 5. Ownership Boundaries

Owned here:

- restating the post-review-approval delta since the original sidecar
  packet;
- confirming (read-only) whether the parent's implementation PR has actually
  reached `ajoe734/execute-plans` `dev` and the hosted dev FE;
- naming the merge/deploy sequencing that `AG-DYNUI-PROD-006` and the
  parent's own closeout must follow.

Not owned here:

- merging or attempting to merge PR #170/#171;
- dispatching `nonprod-deploy.yml` or any other deploy action;
- editing `execute-plans` (vendored mirror or the real repo), BFF routes,
  schemas, registries, or governance code;
- changing `AG-DYNUI-PROD-002`'s status; only its owner (`Claude`) may
  finalize it, and only after the conditions in §4 are met.

---

## 6. Reviewer Handoff

Reviewer (`Codex2`) should verify:

1. This packet is a support artifact only and does not introduce canonical
   contract truth or attempt any merge/deploy action.
2. PR #170/#171 state (closed vs. open/clean/mergeable/CI-green) is read
   correctly and matches `gh pr view` at review time — re-check, since PR
   state can change between packet authoring and review.
3. The claim that the hosted dev FE has not deployed the fix is grounded in
   the `/deployment.json` commit not descending from PR #171, not merely in
   "state looks stale."
4. The packet does not imply the parent or `AG-DYNUI-PROD-006` should force a
   merge or manual deploy; it should route that decision to a human per
   `[[project_agora_pr_self_merge_governance_block]]`.

---

## 7. Verification Notes

Verification was source inspection, a fresh clone of `ajoe734/execute-plans`
`dev`, and anonymous hosted read probing only. No runtime, frontend,
canonical, registry, governance, deploy, merge, or hosted environment changes
were made.

Commands used:

```bash
git status --short
git branch --show-current
git fetch origin dev --quiet
git merge-base --is-ancestor da32e1293 HEAD
git merge-base --is-ancestor da32e1293 origin/dev
./scripts/git/task_start.sh AG-DYNUI-PROD-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2
AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2
AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-002
AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-003
AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-004
AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-005
AI_NAME=Claude python3 scripts/ai_status.py show AG-DYNUI-PROD-006
sed -n '1,260p' docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-002-standalone-workbench-shell.md
gh pr view 170 --repo ajoe734/execute-plans --json number,state,mergedAt,mergeCommit,headRefName,baseRefName,url
gh pr view 171 --repo ajoe734/execute-plans --json number,state,mergedAt,mergeCommit,headRefName,baseRefName,url,statusCheckRollup
gh pr view 171 --repo ajoe734/execute-plans --json number,state,mergeable,mergeStateStatus,isDraft,reviewDecision,updatedAt,createdAt
git ls-remote https://github.com/ajoe734/execute-plans.git dev
git ls-remote https://github.com/ajoe734/execute-plans.git 'task/AG-DYNUI-PROD-002-agora-standalone-shell-compliant'
git clone --branch dev https://github.com/ajoe734/execute-plans.git /tmp/ep-check-followup2
git -C /tmp/ep-check-followup2 log --oneline -5
grep -n "agora\|PlatformShellRoute" /tmp/ep-check-followup2/src/App.tsx
sed -n '170,310p' /tmp/ep-check-followup2/src/App.tsx
cat /tmp/ep-check-followup2/src/routes/agora.tsx
grep -n "workshopId\|ServantDrawer\|placeholder\|workshop context" /tmp/ep-check-followup2/src/agora/TradingDeskLayout.tsx
curl -sS https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json
curl -sS https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health
curl -sS https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/agora/me
sed -n '1,40p' .github/workflows/nonprod-deploy.yml
```
