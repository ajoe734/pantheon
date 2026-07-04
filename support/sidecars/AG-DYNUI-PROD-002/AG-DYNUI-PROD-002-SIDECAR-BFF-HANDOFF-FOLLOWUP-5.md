# AG-DYNUI-PROD-002 Sidecar BFF Handoff Follow-up 5

| Field | Value |
|---|---|
| Task ID | `AG-DYNUI-PROD-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` |
| Parent task | `AG-DYNUI-PROD-002` (Agora standalone workbench shell) |
| Parent owner / reviewer | `Claude` / `Claude2` |
| Sidecar owner / reviewer | `Codex2` / `Codex` |
| Helper kind | `bff_handoff_packet` |
| Prepared | 2026-07-04 |
| Mutates canonical | `false` |

This is a support-only follow-up to `AG-DYNUI-PROD-002-SIDECAR-BFF-HANDOFF.md`,
`...-FOLLOWUP-2.md`, `...-FOLLOWUP-3.md`, and `...-FOLLOWUP-4.md`. It does not
change canonical architecture, L1 contract truth, BFF runtime behavior, route
registries, frontend implementation, deploy configuration, governance policy,
or another task's status by hand.

Follow-up 4 recorded a stale blocker shape: execute-plans PR #171 was still
open, the hosted FE was still undeployed, and `AG-DYNUI-PROD-006` appeared to
be the remaining screenshot gate. That is no longer the current state. The
source PRs merged, hosted proof was captured through the `AG-DYNUI-PROD-003`
evidence packet, and the parent doc now records the dependency-cycle break.
The parent task itself is still `review_approved`, but its remaining work is
owner closeout and PR merge/status finalization, not a BFF query or frontend
handoff gap.

---

## 1. Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates work; support packets do not override L1/L2 architecture or task ownership. |
| `.orchestrator/task-briefs/ag_dynui_prod_002_sidecar_bff_handoff_followup_5.md` | This sidecar is owned by `Codex2`, reviewed by `Codex`, and may create support artifacts only. |
| `.orchestrator/skills/worker-anchor-commit.md` | Meaningful docs/support work must be made durable with an explicit scoped commit. |
| `.orchestrator/skills/task-closeout-finalization.md` | `review_approved` is not terminal; only the task owner can finalize after the approved state is durable and merged. |
| `AI_NAME=Codex2 python3 scripts/ai_status.py show AG-DYNUI-PROD-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` | Live sidecar state is `in_progress`, owner `Codex2`, reviewer `Codex`, artifact path is this file. |
| `AI_NAME=Codex2 python3 scripts/ai_status.py show AG-DYNUI-PROD-002` | Parent remains `review_approved`; status `next` says supervisor resumed it for finalization. The active review notes still mention waiting on `AG-DYNUI-PROD-006`, but the parent artifact now contains later cycle-break evidence. |
| `AI_NAME=Codex2 python3 scripts/ai_status.py show AG-DYNUI-PROD-003` | `AG-DYNUI-PROD-003` is archived `done`; its closeout message records execute-plans PR #173 deployment and pantheon PR #2955 hosted evidence. |
| `AI_NAME=Codex2 python3 scripts/ai_status.py show AG-DYNUI-PROD-005` | Still `todo`, depends on `AG-DYNUI-PROD-002`, `003`, and `004`; this dependency remains blocked only until `002` is formally `done`. |
| `AI_NAME=Codex2 python3 scripts/ai_status.py show AG-DYNUI-PROD-006` | Still `todo`, depends on `005`; it remains the full hosted V10-to-V11 E2E gate, not the remaining shell-architecture proof gate for `002`. |
| `docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-002-standalone-workbench-shell.md` | Now records local shell screenshots, merged PR #171, hosted shell proof from PR #173/#2955, and explicit guidance that `002` should no longer wait on `006` because that creates a dependency cycle. |
| `docs/deployment/evidence/ag-dynui-prod-003/20260704T123434Z/README.md` | Hosted default-route capture was a genuine live no-mock `/agora/trading-room` probe with BFF 200 data and no old inert shell markers; ready-strategy capture used a route mock and belongs to `003`, not `002` shell proof. |
| `gh pr view 171 --repo ajoe734/execute-plans` | PR #171 is `MERGED`, merge commit `467d930957bf109405fa50a5bc252ff66ec3a7ee`, merged at `2026-07-04T12:14:50Z`; integration-gate succeeded. |
| `gh pr view 173 --repo ajoe734/execute-plans` | PR #173 is `MERGED`, merge commit `691f2ec56af9bbc592814563558c001860d8bc7f`, merged at `2026-07-04T12:22:37Z`; integration-gate succeeded. |
| Hosted FE `/deployment.json` | Hosted dev FE reports commit `691f2ec56af9bbc592814563558c001860d8bc7f`, deployed at `20260704T122441Z`, with `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`, and `VITE_BFF_REAL_WRITES=false`. |
| `git -C /home/lupin/code/execute-plans merge-base --is-ancestor ...` | PR #171 merge commit is an ancestor of deployed PR #173 merge commit, and PR #173 is an ancestor of current execute-plans `origin/dev` (`e8fbdc1b1f0676d02e37915c82ec3496f743ac17`). |
| `gh pr view 2955 --repo ajoe734/pantheon` | Pantheon evidence PR #2955 is `MERGED`, merge commit `3ff65b566a7ebca0ef2f151d96952fb255285938`, with branch checks successful. |
| `gh pr view 2959 --repo ajoe734/pantheon` | Pantheon cycle-break PR #2959 is `MERGED`, merge commit `efcc321acb3d4772a5ba6d3baf836f29e7183745`, with branch checks successful. |
| `gh pr view 2968 --repo ajoe734/pantheon` | Parent closeout PR #2968 is `OPEN`, `MERGEABLE`, `mergeStateStatus=BLOCKED`, auto-merge enabled by `ajoe734`, with branch checks still queued at this check. |

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned.

---

## 2. Delta Since Follow-up 4

| Area | Follow-up 4 | Current follow-up |
|---|---|---|
| execute-plans PR #171 | `OPEN`, clean/mergeable/green, blocked on human self-merge governance. | `MERGED` at `467d930957bf109405fa50a5bc252ff66ec3a7ee`. |
| execute-plans PR #173 | Not the blocker resolution yet. | `MERGED` at `691f2ec56af9bbc592814563558c001860d8bc7f`, and the hosted FE deployed that exact commit. |
| Hosted dev FE | Still running pre-fix `dd597405...` bundle. | `/deployment.json` reports deployed commit `691f2ec56af9bbc592814563558c001860d8bc7f`, which contains #171. |
| Hosted shell proof | Missing; packet expected `AG-DYNUI-PROD-006` to produce screenshots. | `AG-DYNUI-PROD-003` evidence PR #2955 now contains the relevant live no-mock `/agora/trading-room` hosted capture. Parent doc PR #2959 records that this breaks the `002` -> `006` wait cycle. |
| Parent `AG-DYNUI-PROD-002` status | `review_approved`, unable to advance without human merge/deploy/proof. | Still `review_approved`, but now has merged source, hosted proof, cycle-break doc, and an open parent closeout PR #2968. Remaining gate is parent owner finalization, not new support analysis. |
| `AG-DYNUI-PROD-003` | `review_approved`, similar missing-hosted-proof shape. | `done`; its hosted evidence is now the proof source relevant to `002` shell closeout. |
| `AG-DYNUI-PROD-005` / `006` | `005` and `006` both `todo`; `006` was treated as the possible missing proof source. | `005` and `006` are still `todo`, but `006` should remain the full E2E gate after `005`, not a prerequisite for `002` shell closeout. |

Net finding: the repeated "wait for PR #171 merge and hosted screenshots"
support loop should stop for `AG-DYNUI-PROD-002`. The current evidence points
to one concrete remaining action: let the parent owner closeout PR #2968 pass
checks, merge, and then have the owner run the normal `done` transition.

No sidecar-owned code, BFF, route, registry, deploy, governance, or task-state
change is made here.

---

## 3. BFF Query Surface And Gap Matrix

No new BFF contract need is introduced by the updated state.

- The parent shell source change already preserves strict BFF usage by keeping
  `/agora/*` inside the app providers while removing Management shell chrome.
- The hosted default-route evidence used the live BFF and received a real
  200 response for the tenant scope.
- The Trading Room proposal/workspace/widget-revision/version/rollback route
  family remains owned by `AG-DYNUI-PROD-005` and the final hosted workflow by
  `AG-DYNUI-PROD-006`.
- The ready-strategy evidence in the `AG-DYNUI-PROD-003` packet used a
  route-level mock because the live dev tenant had zero strategies and real
  writes are disabled. That mock is not needed to prove the `002` shell no
  longer renders the old inert Management/Trading Desk shell; the no-mock
  default-route screenshot is the relevant proof for this parent.

Therefore the handoff guidance from the original packet remains accurate:
shell closeout should not add ad hoc BFF fetches or bypass the existing BFF
client modules, and this sidecar should not request new BFF routes.

---

## 4. Operator Journey Update

The current journey for `AG-DYNUI-PROD-002` closeout is:

1. Treat execute-plans PR #171 as merged source truth for the standalone Agora
   shell.
2. Treat execute-plans PR #173 and hosted `/deployment.json` as evidence that
   the hosted dev FE deployed a build containing #171.
3. Treat `docs/deployment/evidence/ag-dynui-prod-003/20260704T123434Z/` as the
   hosted shell proof source for `002` only for the no-mock default-route
   capture.
4. Treat pantheon PR #2959 as the parent doc update that records why `002`
   must not keep waiting on `006` before `005` can start.
5. Watch parent closeout PR #2968. It was open with auto-merge enabled and
   checks queued at this packet's re-check. If it merges, the parent owner can
   use the normal closeout finalization path to move `AG-DYNUI-PROD-002` from
   `review_approved` to `done`.

What should not happen:

- Do not re-open the old self-merge blocker for PR #171; it is merged.
- Do not treat `AG-DYNUI-PROD-006` as the remaining shell screenshot gate for
  `002`; it depends on `005`, and `005` depends on `002`.
- Do not use the route-mocked ready-strategy capture as live shell proof. It
  is acceptable `003` workflow evidence, but the no-mock default-route capture
  is the clean `002` proof.
- Do not change `AG-DYNUI-PROD-002`, `005`, or `006` status from this sidecar.

---

## 5. Ownership Boundaries

Owned here:

- independently re-checking the state change since Follow-up 4;
- documenting that the old PR #171/deploy/screenshot blocker has been replaced
  by parent closeout PR #2968 and owner finalization;
- preserving a support-only handoff packet for reviewer `Codex`.

Not owned here:

- modifying execute-plans, BFF runtime, schemas, registries, governance, or
  canonical docs;
- attempting to merge or manage parent PR #2968;
- moving `AG-DYNUI-PROD-002` to `done`;
- deciding `AG-DYNUI-PROD-005` or `AG-DYNUI-PROD-006` dispatch timing beyond
  noting that `002` no longer has a shell-proof dependency on `006`.

---

## 6. Reviewer Handoff

Reviewer (`Codex`) should verify:

1. This packet is support-only and the diff is limited to the task brief plus
   this sidecar packet.
2. PR #171 and PR #173 are correctly recorded as merged, and hosted
   `/deployment.json` still points at a commit containing #171.
3. PR #2955 and PR #2959 are correctly recorded as merged evidence/doc updates.
4. PR #2968 is still the current parent closeout surface, or has merged since
   this packet was written.
5. The packet does not overclaim `AG-DYNUI-PROD-006`: full V10-to-V11 hosted
   E2E remains `006`; only the shell screenshot gate for `002` is no longer
   blocked on `006`.

Recommended review outcome if the re-checks still match: approve this sidecar
packet and return it to owner `Codex2` for normal task closeout. The parent
owner remains responsible for `AG-DYNUI-PROD-002` finalization.

---

## 7. Verification Notes

Verification was source/status inspection, GitHub PR read probing, git
ancestor checks, and anonymous hosted read probing only. No runtime, frontend,
canonical, registry, governance, deploy, merge, or hosted environment changes
were made.

Commands used:

```bash
git status -sb
git branch --show-current
git remote -v
git fetch origin dev
git merge --ff-only origin/dev
sed -n '1,240p' AI_COLLABORATION_GUIDE.md
sed -n '1,240p' .orchestrator/task-briefs/ag_dynui_prod_002_sidecar_bff_handoff_followup_5.md
sed -n '1,240p' .orchestrator/skills/worker-anchor-commit.md
sed -n '1,260p' .orchestrator/skills/task-closeout-finalization.md
sed -n '1,240p' ai-status.json
AI_NAME=Codex2 python3 scripts/ai_status.py show AG-DYNUI-PROD-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-5
AI_NAME=Codex2 python3 scripts/ai_status.py show AG-DYNUI-PROD-002
AI_NAME=Codex2 python3 scripts/ai_status.py show AG-DYNUI-PROD-003
AI_NAME=Codex2 python3 scripts/ai_status.py show AG-DYNUI-PROD-005
AI_NAME=Codex2 python3 scripts/ai_status.py show AG-DYNUI-PROD-006
sed -n '1,360p' support/sidecars/AG-DYNUI-PROD-002/AG-DYNUI-PROD-002-SIDECAR-BFF-HANDOFF.md
sed -n '1,260p' support/sidecars/AG-DYNUI-PROD-002/AG-DYNUI-PROD-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md
sed -n '1,260p' support/sidecars/AG-DYNUI-PROD-002/AG-DYNUI-PROD-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md
sed -n '1,280p' support/sidecars/AG-DYNUI-PROD-002/AG-DYNUI-PROD-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md
sed -n '1,340p' docs/bff/execution-tasks/2026-07-03-agora-dynui-production-gap/AG-DYNUI-PROD-002-standalone-workbench-shell.md
sed -n '1,260p' docs/deployment/evidence/ag-dynui-prod-003/20260704T123434Z/README.md
gh pr view 171 --repo ajoe734/execute-plans --json number,state,mergedAt,mergeCommit,headRefName,baseRefName,url,statusCheckRollup,mergeable,mergeStateStatus,reviewDecision,autoMergeRequest
gh pr view 173 --repo ajoe734/execute-plans --json number,state,mergedAt,mergeCommit,headRefName,baseRefName,url,statusCheckRollup,mergeable,mergeStateStatus,reviewDecision,autoMergeRequest
gh pr view 2955 --repo ajoe734/pantheon --json number,state,mergedAt,mergeCommit,headRefName,baseRefName,url,statusCheckRollup,mergeable,mergeStateStatus,reviewDecision,autoMergeRequest
gh pr view 2959 --repo ajoe734/pantheon --json number,state,mergedAt,mergeCommit,headRefName,baseRefName,url,title,statusCheckRollup
gh pr view 2968 --repo ajoe734/pantheon --json number,state,mergedAt,mergeCommit,headRefName,baseRefName,url,title,statusCheckRollup,mergeable,mergeStateStatus,reviewDecision,autoMergeRequest
git ls-remote https://github.com/ajoe734/execute-plans.git dev
git -C /home/lupin/code/execute-plans fetch origin dev
git -C /home/lupin/code/execute-plans merge-base --is-ancestor 467d930957bf109405fa50a5bc252ff66ec3a7ee 691f2ec56af9bbc592814563558c001860d8bc7f
git -C /home/lupin/code/execute-plans merge-base --is-ancestor 691f2ec56af9bbc592814563558c001860d8bc7f e8fbdc1b1f0676d02e37915c82ec3496f743ac17
curl -sS https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json
find docs/deployment/evidence/ag-dynui-prod-003/20260704T123434Z -maxdepth 2 -type f
gh pr list --repo ajoe734/pantheon --search "AG-DYNUI-PROD-002" --state open --json number,title,state,headRefName,url
```
