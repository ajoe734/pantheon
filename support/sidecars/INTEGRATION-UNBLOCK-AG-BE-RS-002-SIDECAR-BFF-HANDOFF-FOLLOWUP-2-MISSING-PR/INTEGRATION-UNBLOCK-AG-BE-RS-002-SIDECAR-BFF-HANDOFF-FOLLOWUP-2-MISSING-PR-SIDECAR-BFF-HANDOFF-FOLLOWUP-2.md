# INTEGRATION-UNBLOCK-AG-BE-RS-002 Followup-2 Missing-PR BFF/Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Helper parent | `INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR` |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Claude` / `Codex` |
| Sidecar owner / reviewer | `Codex` / `Claude` |
| Date | `2026-06-21` |
| Status | `review handoff prepared` |
| Current Pantheon dev base after branch refresh | `f7ee9a94e962f8f50b29dd265ccceafad028cd65` |
| Original followup-2 task | `AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Original followup-2 PR | `#2100`, `MERGED`, merge commit `fb48ffff595898152e0451e39615547570862053` |
| First missing-pr handoff sidecar | `INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR-SIDECAR-BFF-HANDOFF`, archived `done`, PR `#2105`, merge commit `1fa382194e1306d0e48ad593649b469a1c69f215` |
| Parent unblock PR | `#2106`, `MERGED`, merge commit `f7ee9a94e962f8f50b29dd265ccceafad028cd65`, head commit `a583cfca6e279735f383c74b2b1bf7b27bcd4478` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI or JSON schema truth, BFF runtime code, route
registries, governance policy, database migrations, OpenClaw adapter code, or
`execute-plans` source files.

## 1. Purpose

This followup-2 sidecar refreshes the support packet for the
`INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR`
parent after the first support sidecar merged.

The integration facts have advanced:

- the original `AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` task is still not
  missing its PR: PR `#2100` is merged into `dev`
- the first support-only BFF/frontend handoff sidecar is complete: PR `#2105`
  is merged into `dev`
- the parent unblock task now has its own merged PR, `#2106`, with merge
  commit `f7ee9a94e962f8f50b29dd265ccceafad028cd65`
- the parent task is `review_approved`, so the remaining parent action is owner
  status closeout after confirming the merged PR evidence, not more
  BFF/runtime design work

This sidecar does not approve, reopen, supersede, or finalize the parent
unblock task.

## 2. Sources Rechecked

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates work; support packets do not override canonical truth or product semantics. |
| `.orchestrator/task-briefs/integration_unblock_ag_be_rs_002_sidecar_bff_handoff_followup_2_missing_pr_sidecar_bff_handoff_followup_2.md` | Sidecar is support-only: prepare BFF query gap, operator journey, and frontend handoff materials without canonical changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support changes require explicit scope and narrow commit discipline. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo changes require task commit, PR, merge, and owner closeout before `done`; review handoff is not final closeout. |
| `AI_NAME=Codex ./scripts/ai-status.sh show INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | This task is active `in_progress`; owner `Codex`, reviewer `Claude`; artifact is this packet. |
| `AI_NAME=Codex ./scripts/ai-status.sh show INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR` | Parent unblock task remains `review_approved`; owner `Claude`, reviewer `Codex`; prior review note predates the `#2106` merge. |
| `AI_NAME=Codex ./scripts/ai-status.sh show INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR-SIDECAR-BFF-HANDOFF` | First support sidecar is archived `done`; PR `#2105` merged into `dev`; dirty delivery metadata was limited to one generated task brief in that worktree. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | Original followup-2 is archived `done`; archive records PR `#2100`, merge commit `fb48ffff`, and task branch head `3208bfca`. |
| `gh pr view 2100 --repo ajoe734/pantheon --json ...` | PR `#2100` is `MERGED`; base `dev`; merged at `2026-06-21T16:06:38Z`; visible checks succeeded. |
| `gh pr view 2106 --repo ajoe734/pantheon --json ...` | PR `#2106` is `MERGED`; merged at `2026-06-21T16:24:48Z`; merge commit `f7ee9a94`; visible check runs reported `SUCCESS`. |
| `gh pr diff 2106 --repo ajoe734/pantheon --name-only` | PR `#2106` changes only `.orchestrator/task-briefs/integration_unblock_ag_be_rs_002_sidecar_bff_handoff_followup_2_missing_pr.md`. |
| `git ls-remote --heads origin ...` | Original followup-2 branch exists at `3208bfca`; parent unblock branch exists at `a583cfca`; no remote head was found for this followup-2 sidecar branch before this packet is pushed. |
| `support/sidecars/INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR/INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR-SIDECAR-BFF-HANDOFF.md` | Prior support packet recorded the original missing-pr correction and downstream BFF/frontend facts; this packet refreshes it with current parent PR `#2106` state. |
| `support/sidecars/AG-BE-RS-002/AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` | Corrected frontend/BFF handoff baseline: `research.run.progress`, dispatch `202` queued-confirmation, cancel `409` for terminal states, `If-Match`/`Idempotency-Key` headers, and 26-field `ResearchRunProjection`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001` | Frontend research card task remains `todo` and still depends on `AG-FE-SW-002`, `AG-BE-RS-002`, and `AG-XR-OPENAPI-004`. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## 3. Current Integration State

| Item | Current state | Handoff implication |
|---|---|---|
| Original sidecar followup-2 | archived `done`; PR `#2100` merged into `dev`; merge commit `fb48ffff`; visible checks green. | The original sidecar should not be treated as lacking a PR. |
| First missing-pr BFF/frontend handoff sidecar | archived `done`; PR `#2105` merged into `dev`; merge commit `1fa38219`. | Parent owner can cite that packet for support-only BFF/frontend disposition. |
| Parent missing-pr unblock task | `review_approved`; PR `#2106` is merged into `dev` at `f7ee9a94`; status closeout has not been observed in `ai-status.sh show`. | Parent owner can now run owner closeout if no newer blocker exists. |
| This followup-2 sidecar | active `in_progress`; owner `Codex`; reviewer `Claude`; no remote head found before push. | This packet should be reviewed as a support refresh, then merged through its own task PR if accepted. |
| `AG-BE-RS-002` | archived `done`; research run/progress/result projection landed. | Downstream frontend may use the corrected BFF contract facts after its other dependencies are satisfied. |
| `AG-FE-RS-001` | `todo`; still depends on `AG-FE-SW-002`, `AG-BE-RS-002`, and `AG-XR-OPENAPI-004`. | Do not call the frontend research card task fully unblocked solely because `AG-BE-RS-002` is done. |

## 4. BFF Query Gap Ledger

This followup-2 support sidecar does not identify a new BFF runtime gap.
Runtime/query facts remain those in the merged `AG-BE-RS-002` followup-2
packet and the first missing-pr handoff packet.

| Surface | Current disposition | Parent handoff |
|---|---|---|
| Missing PR for original followup-2 | Not reproduced. PR `#2100` exists and is merged. | Parent should keep this as the primary root-cause correction for the auto-integrator blocker. |
| Missing PR for the parent unblock task | No longer true. PR `#2106` exists and is merged. | Parent closeout blocker has moved from PR discovery/merge to owner status finalization if the task still shows `review_approved`. |
| Extra BFF query or endpoint gap | None found in this support pass. | Do not open BFF runtime, registry, schema, or governance changes from this sidecar. |
| Research run dispatch | Implemented by `AG-BE-RS-002`; dispatch returns `202` queued-confirmation, not full projection. | `AG-FE-RS-001` must call `GET /bff/agora/research-runs/{run_id}` after dispatch. |
| Research run progress SSE | Implemented as `research.run.progress`. | Frontend must not subscribe for `workshop.research.progress` for this surface. |
| Run cancel semantics | Implemented with `202` accepted for cancellable runs and `409` for terminal statuses. | Frontend must catch `409`, refresh, and display current terminal state rather than treating cancel as success. |
| Plan concurrency | Dispatch requires `Idempotency-Key` and `If-Match`; `If-Match` comes from plan detail `meta.etag`. | Frontend write controls need explicit idempotency/concurrency handling. |
| Artifact/evidence listing | `GET /bff/agora/research-runs/{run_id}/artifacts` returns a list envelope; evidence refs are appended as stored. | Frontend should not assume every item has one uniform artifact shape. |

## 5. Operator Journey For The Parent Closeout

### Journey A: Parent Owner Finalizes The Missing-PR Unblock

1. Parent owner checks `AI_NAME=Claude ./scripts/ai-status.sh show INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR`.
2. Parent owner confirms the task is `review_approved`, not terminal `done`.
3. Parent owner checks PR `#2106` and verifies merge commit
   `f7ee9a94e962f8f50b29dd265ccceafad028cd65` is on `dev`.
4. If the task still shows `review_approved`, parent owner runs the closeout
   checklist; do not bypass it by editing state directly.
5. Parent owner then runs
   `AI_NAME=Claude ./scripts/ai-status.sh done ...` with the merge evidence.
6. Parent owner does not reopen or mutate the original followup-2 packet unless
   a new factual mismatch is discovered.

### Journey B: Downstream Frontend Uses The Corrected Research Handoff

1. Operator opens an approved research plan in the strategy workshop UI.
2. Frontend reads plan detail through the BFF client and stores `meta.etag`.
3. Frontend dispatches via `POST /bff/agora/research-plans/{plan_id}/runs` with
   `Idempotency-Key` and `If-Match`.
4. BFF returns `202` queued-confirmation with `run_id`; UI shows queued state
   only.
5. Frontend calls `GET /bff/agora/research-runs/{run_id}` for the full
   `ResearchRunProjection`.
6. Frontend updates progress from `research.run.progress` SSE events or
   polling.
7. UI renders `research_progress` only for `queued`, `dispatching`, or
   `running`; renders `research_result` only for `succeeded`.
8. UI never renders promotion, canary, RuntimeBinding, capital, broker, or
   order controls from research responses.

### Journey C: Cancel Or Terminal-State Refresh

1. Operator selects cancel for an in-flight research run.
2. Frontend sends `POST /bff/agora/research-runs/{run_id}/cancel` with
   `Idempotency-Key`.
3. On `202`, UI refreshes the run and shows `execution_status=cancelled`.
4. On `409`, UI refreshes the run detail and shows the current terminal status;
   it must not report cancel success unless the refreshed projection confirms
   cancellation.

## 6. Frontend Handoff Summary

The frontend handoff remains the merged
`support/sidecars/AG-BE-RS-002/AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md`
packet. The minimum client contract for `AG-FE-RS-001` is:

| Client method | BFF behavior to preserve |
|---|---|
| `listResearchPlanRuns(planId)` | Returns list envelope with full `ResearchRunProjection` items. |
| `dispatchResearchPlan(planId, idempotencyKey, ifMatch)` | Returns `202` queued-confirmation envelope; caller must fetch full run projection after response. |
| `getResearchRun(runId)` | Returns `ResearchRunProjection` directly, not a wrapper envelope. |
| `cancelResearchRun(runId, idempotencyKey)` | Returns `202` for accepted cancellation; returns `409` for terminal states. |
| `listResearchRunArtifacts(runId)` | Returns artifact/evidence refs in a list envelope; evidence refs are stored verbatim. |

Client and UI constraints:

- Use BFF strict live transport only; no local fixture fallback and no direct
  research-service fanout.
- Include `Idempotency-Key` for writes and `If-Match` for plan-scoped mutation
  endpoints that require ETag concurrency.
- Treat `backend.mode=fixture|stub` as visible non-production proof.
- Preserve `no_order_route_proof="research_only_not_direct_action"` as a hard
  no-order invariant.
- Keep `AG-FE-RS-001` blocked until all of its dependencies, including
  `AG-FE-SW-002`, are satisfied.

## 7. Parent Absorption Checklist

Claude should not absorb this followup-2 sidecar into parent closeout until the
parent evidence answers these checks:

| Check | Required evidence |
|---|---|
| Original missing-pr disposition | PR `#2100` is recorded as the existing merged PR for `AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`. |
| Prior support packet disposition | PR `#2105` is recorded as the merged support-only BFF/frontend handoff packet for the parent unblock context. |
| Parent unblock PR disposition | PR `#2106` is merged into `dev`; if the parent task still shows `review_approved`, owner closeout remains. |
| No runtime expansion | Parent does not edit BFF runtime, OpenAPI, schemas, canonical docs, registry/governance, or execute-plans as part of this missing-pr support closure. |
| Frontend handoff reuse | Parent points downstream frontend work to the already-merged followup-2 corrections rather than creating a conflicting BFF shape. |
| Dependency honesty | Parent does not state that `AG-FE-RS-001` is fully unblocked while `AG-FE-SW-002` remains incomplete. |
| Status honesty | Parent does not present this followup-2 sidecar packet as review approval or finalization of the parent unblock task. |

## 8. Verification Performed For This Sidecar

Commands and results:

| Command | Result |
|---|---|
| `git status -sb`; `git branch --show-current`; `git remote -v` | Started on expected sidecar branch with `origin` remote; only generated task brief was dirty before packet creation. |
| `AI_NAME=Codex ./scripts/ai-status.sh show INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | Active `in_progress`; owner `Codex`; reviewer `Claude`; artifact path is this file. |
| `AI_NAME=Codex ./scripts/ai-status.sh show INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR` | Parent active `review_approved`; owner `Claude`; reviewer `Codex`; prior review note predates the `#2106` merge. |
| `AI_NAME=Codex ./scripts/ai-status.sh show INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR-SIDECAR-BFF-HANDOFF` | First missing-pr support sidecar archived `done`; PR `#2105` merged into `dev`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | Source `archive`; terminal status `done`; PR `#2100`; merge commit `fb48ffff`. |
| `gh pr view 2100 --repo ajoe734/pantheon --json ...` | PR `#2100` is `MERGED`; merged at `2026-06-21T16:06:38Z`; visible checks succeeded. |
| `gh pr view 2106 --repo ajoe734/pantheon --json ...` | PR `#2106` is `MERGED`; merged at `2026-06-21T16:24:48Z`; merge commit `f7ee9a94`; visible checks reported `SUCCESS`. |
| `gh pr diff 2106 --repo ajoe734/pantheon --name-only` | PR `#2106` changes only the parent missing-pr task brief. |
| `git ls-remote --heads origin ...` | Original followup-2 branch exists at `3208bfca`; parent unblock branch exists at `a583cfca`; no remote head found for this sidecar before push. |
| `git merge --ff-only origin/dev` | Fast-forwarded this sidecar branch from `1fa38219` to `origin/dev` `1be6a116` before packet creation. |
| `git merge --no-edit origin/dev` | Refreshed this sidecar branch after PR `#2106` merged, bringing in `origin/dev` `f7ee9a94`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001` | Source `active`; status `todo`; dependencies include `AG-FE-SW-002`, `AG-BE-RS-002`, and `AG-XR-OPENAPI-004`. |

## 9. Handoff To Reviewer

Reviewer `Claude`: please review this support-only packet for factual accuracy
and scope discipline. The recommended disposition is to approve the sidecar if
the PR/status facts match current state, while keeping parent unblock closeout
with the parent owner.

Suggested reviewer command after approval:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR/INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md \
  REVIEW_NOTES_ZH="Support-only followup-2 BFF/frontend handoff approved: original AG-BE-RS-002 followup-2 is not missing a PR because PR #2100 is merged into dev; prior support sidecar PR #2105 is merged; parent unblock PR #2106 is also merged and remaining parent work is owner status closeout if the task still shows review_approved; no BFF/runtime/canonical/frontend implementation changes are introduced by this packet." \
  ./scripts/ai-status.sh approve INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 \
  "Support-only followup-2 missing-PR BFF/frontend handoff packet approved for parent unblock owner."
```

Suggested reviewer command if changes are required:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 \
  "Describe the factual correction, missing PR evidence, or handoff boundary issue required before approval."
```
