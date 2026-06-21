# INTEGRATION-UNBLOCK-AG-BE-RS-002 Followup-2 Missing-PR BFF/Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR-SIDECAR-BFF-HANDOFF` |
| Helper parent | `INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR` |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Claude` / `Codex` |
| Sidecar owner / reviewer | `Codex` / `Claude` |
| Date | `2026-06-21` |
| Status | `review handoff prepared` |
| Current Pantheon dev base after fetch | `88c7ecd4f62a96d56243677137b36e9bec598b32` |
| Original followup-2 task | `AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Original followup-2 PR | `#2100`, `MERGED`, merge commit `fb48ffff595898152e0451e39615547570862053` |
| Original followup-2 task state | archived `done` at `2026-06-21T16:06:52Z` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI or JSON schema truth, BFF runtime code, route
registries, governance policy, database migrations, OpenClaw adapter code, or
`execute-plans` source files.

## 1. Purpose

This sidecar gives the parent unblock owner a compact handoff for the
`missing-pr` integration blocker raised against
`AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`.

The current evidence says the original followup-2 task is not missing its PR:
GitHub PR `#2100` exists, is merged into `dev`, and has green visible checks.
The remaining parent work is therefore integration bookkeeping: record the root
cause/disposition, decide whether the auto-integrator task should be resolved by
the existing PR evidence or by a small superseding resolution PR, and keep the
downstream BFF/frontend handoff facts aligned with the already-merged
followup-2 packet.

This sidecar does not approve, reopen, or finalize the parent unblock task.

## 2. Sources Rechecked

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates work; support packets do not override canonical truth. |
| `.orchestrator/task-briefs/integration_unblock_ag_be_rs_002_sidecar_bff_handoff_followup_2_missing_pr_sidecar_bff_handoff.md` | Sidecar is support-only: prepare BFF query gap, operator journey, and frontend handoff materials without canonical changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support changes require explicit scope and narrow commit discipline. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo changes require task commit, PR, merge, and owner closeout before `done`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR-SIDECAR-BFF-HANDOFF` | Sidecar is active `in_progress`; owner `Codex`, reviewer `Claude`; artifact is this packet. |
| `AI_NAME=Codex ./scripts/ai-status.sh show INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR` | Parent unblock task is active `in_progress`; owner `Claude`, reviewer `Codex`; acceptance is root-cause documentation, original PR update/supersession, and avoiding stranded review-approved state. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | Original followup-2 is archived `done`; archive records PR `#2100`, merge commit `fb48ffff`, and task branch head `3208bfca`. |
| `gh pr view 2100 --repo ajoe734/pantheon --json ...` | PR `#2100` is `MERGED`; base `dev`; head `task/AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`; merged at `2026-06-21T16:06:38Z`; visible checks succeeded. |
| `gh pr list --repo ajoe734/pantheon --head task/AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 --state all ...` | Returns PR `#2100`; confirms the original followup-2 branch is not PR-less. |
| `gh pr list --repo ajoe734/pantheon --head task/INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR --state all ...` | Returns no PR for the parent unblock branch at packet time. |
| `git ls-remote --heads origin ...` | Original followup-2 branch still exists at `3208bfca`; no remote heads found for the parent unblock branch or this sidecar branch before this sidecar is pushed. |
| `support/sidecars/AG-BE-RS-002/AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` | Corrected frontend/BFF handoff baseline: `research.run.progress`, dispatch `202` queued-confirmation, cancel `409` for terminal states, `If-Match`/`Idempotency-Key` headers, and 26-field `ResearchRunProjection`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-002` | Parent implementation is archived `done`; implementation PR `#2092` and closeout PR `#2094` are merged; 173 focused tests passed in closeout. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001` | Frontend research card task remains `todo` and still depends on `AG-FE-SW-002`, `AG-BE-RS-002`, and `AG-XR-OPENAPI-004`. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## 3. Current Integration State

| Item | Current state | Handoff implication |
|---|---|---|
| Original sidecar followup-2 | archived `done`; PR `#2100` merged into `dev`; merge commit `fb48ffff`; visible checks green. | The original sidecar should not be treated as lacking a PR. |
| Parent missing-pr unblock task | active `in_progress`; owner `Claude`; no PR found for branch `task/INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR` at packet time. | Parent owner should record whether the blocker is superseded by PR `#2100` evidence or should be closed through a small resolution PR. |
| This sidecar | active `in_progress`; owner `Codex`; reviewer `Claude`. | This packet is advisory support for parent closeout/review. |
| `AG-BE-RS-002` | archived `done`; research run/progress/result projection landed. | Downstream frontend may consume the BFF contract facts from followup-2 after its other dependencies are satisfied. |
| `AG-FE-RS-001` | `todo`; still depends on `AG-FE-SW-002`, `AG-BE-RS-002`, and `AG-XR-OPENAPI-004`. | Do not call the frontend research card task fully unblocked solely because `AG-BE-RS-002` is done. |

## 4. BFF Query Gap Ledger

This missing-PR unblock sidecar does not identify a new BFF runtime gap.
Runtime/query facts remain those in the merged followup-2 packet.

| Surface | Current disposition | Parent handoff |
|---|---|---|
| Missing PR for original followup-2 | Not reproduced. PR `#2100` exists and is merged. | Parent should document this as the primary root-cause correction for the auto-integrator blocker. |
| Missing PR for the parent unblock task | No PR found for `task/INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR` at packet time. | Parent may need a small resolution PR if the unblock task itself changes status/docs, or may supersede the task if existing PR `#2100` fully resolves the integration concern. |
| Research run dispatch | Implemented by `AG-BE-RS-002`; dispatch returns `202` queued-confirmation, not full projection. | `AG-FE-RS-001` must call `GET /bff/agora/research-runs/{run_id}` after dispatch. |
| Research run progress SSE | Implemented as `research.run.progress`. | Frontend must not subscribe for `workshop.research.progress` for this surface. |
| Run cancel semantics | Implemented with `202` accepted for cancellable runs and `409` for terminal statuses. | Frontend must catch `409`, refresh, and display current terminal state rather than treating cancel as a no-op. |
| Plan concurrency | Dispatch requires `Idempotency-Key` and `If-Match`; `If-Match` comes from plan detail `meta.etag`. | Frontend write controls need explicit idempotency/concurrency handling. |
| Artifact/evidence listing | `GET /bff/agora/research-runs/{run_id}/artifacts` returns a list envelope; evidence refs are appended as stored. | Frontend should not assume every item has one uniform artifact shape. |
| Research card frontend client | `execute-plans/src/lib/bff-v1/agora/research.ts` remains an `AG-FE-RS-001` deliverable. | This sidecar only forwards handoff guidance; it does not edit execute-plans. |

## 5. Operator Journey For The Missing-PR Unblock

### Journey A: Parent Owner Resolves The Integration Blocker

1. Parent owner checks `AI_NAME=Claude ./scripts/ai-status.sh show AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`.
2. Parent owner confirms the archive records terminal `done`, PR `#2100`, branch head `3208bfca`, and merge commit `fb48ffff`.
3. Parent owner checks `gh pr view 2100 --repo ajoe734/pantheon` and verifies state `MERGED`, base `dev`, and green visible checks.
4. Parent owner records that the original followup-2 task is not actually missing a PR.
5. If the auto-integrator task still needs its own durable artifact, parent owner opens a small task PR for the integration-unblock resolution; otherwise parent owner can supersede/close the unblock task with the PR `#2100` evidence, depending on reviewer policy.
6. Parent owner does not reopen or mutate the original followup-2 packet unless a new factual mismatch is discovered.

### Journey B: Downstream Frontend Uses The Corrected Research Handoff

1. Operator opens an approved research plan in the strategy workshop UI.
2. Frontend reads plan detail through the BFF client and stores `meta.etag`.
3. Frontend dispatches via `POST /bff/agora/research-plans/{plan_id}/runs` with `Idempotency-Key` and `If-Match`.
4. BFF returns `202` queued-confirmation with `run_id`; UI shows queued state only.
5. Frontend calls `GET /bff/agora/research-runs/{run_id}` for the full `ResearchRunProjection`.
6. Frontend updates progress from `research.run.progress` SSE events or polling.
7. UI renders `research_progress` only for `queued`, `dispatching`, or `running`; renders `research_result` only for `succeeded`.
8. UI never renders promotion, canary, RuntimeBinding, capital, broker, or order controls from research responses.

### Journey C: Cancel Or Terminal-State Refresh

1. Operator selects cancel for an in-flight research run.
2. Frontend sends `POST /bff/agora/research-runs/{run_id}/cancel` with `Idempotency-Key`.
3. On `202`, UI refreshes the run and shows `execution_status=cancelled`.
4. On `409`, UI refreshes the run detail and shows the current terminal status; it must not report cancel success unless the refreshed projection confirms cancellation.

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

Claude should not absorb this sidecar into the parent unblock closeout until the
parent evidence answers these checks:

| Check | Required evidence |
|---|---|
| Original missing-pr disposition | PR `#2100` is recorded as the existing merged PR for `AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`. |
| Parent unblock path | Parent either opens/merges a small resolution PR or records an approved supersession/closeout path that explains why PR `#2100` resolves the auto-integrator blocker. |
| No runtime expansion | Parent does not edit BFF runtime, OpenAPI, schemas, canonical docs, registry/governance, or execute-plans as part of this missing-pr support closure. |
| Frontend handoff reuse | Parent points downstream frontend work to the already-merged followup-2 corrections rather than creating a conflicting BFF shape. |
| Dependency honesty | Parent does not state that `AG-FE-RS-001` is fully unblocked while `AG-FE-SW-002` remains incomplete. |
| Status honesty | Parent does not present this sidecar packet as review approval or finalization of the parent unblock task. |

## 8. Verification Performed For This Sidecar

Commands and results:

| Command | Result |
|---|---|
| `git status -sb`; `git branch --show-current`; `git remote -v` | Started on expected sidecar branch with `origin` remote; only generated task brief was dirty before packet creation. |
| `AI_NAME=Codex ./scripts/ai-status.sh start INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR-SIDECAR-BFF-HANDOFF ...` | Recorded sidecar start in shared status; no local `ai-status.json` diff. |
| `AI_NAME=Codex ./scripts/ai-status.sh show INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR-SIDECAR-BFF-HANDOFF` | Active `in_progress`; owner `Codex`; reviewer `Claude`; artifact path is this file. |
| `AI_NAME=Codex ./scripts/ai-status.sh show INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR` | Parent active `in_progress`; owner `Claude`; reviewer `Codex`; depends on original followup-2. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | Source `archive`; terminal status `done`; PR `#2100`; merge commit `fb48ffff`. |
| `gh pr view 2100 --repo ajoe734/pantheon --json ...` | PR `#2100` is `MERGED`; merged at `2026-06-21T16:06:38Z`; visible checks succeeded. |
| `gh pr list --repo ajoe734/pantheon --head task/AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 --state all ...` | Returned PR `#2100`, confirming the original branch has a PR. |
| `gh pr list --repo ajoe734/pantheon --head task/INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR --state all ...` | Returned `[]`; no PR found for the parent unblock branch at packet time. |
| `git ls-remote --heads origin ...` | Original followup-2 branch exists at `3208bfca`; no remote head found for parent unblock branch or this sidecar before sidecar push. |
| `git fetch origin dev`; `git merge --ff-only origin/dev` | Fast-forwarded this sidecar branch from `fb48ffff` to current `origin/dev` `88c7ecd4`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-002` | Source `archive`; terminal status `done`; implementation and closeout PRs merged. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001` | Source `active`; status `todo`; dependencies include `AG-FE-SW-002`, `AG-BE-RS-002`, and `AG-XR-OPENAPI-004`. |

## 9. Handoff To Reviewer

Reviewer `Claude`: please review this support-only packet for factual accuracy
and scope discipline. The recommended disposition is to approve the sidecar if
the PR/status facts match current state, while keeping parent unblock closeout
with the parent owner.

Suggested reviewer command after approval:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR/INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR-SIDECAR-BFF-HANDOFF.md \
  REVIEW_NOTES_ZH="Support-only BFF/frontend handoff approved: original AG-BE-RS-002 followup-2 is not missing a PR because PR #2100 is merged into dev with green visible checks; parent missing-pr task should resolve integration bookkeeping without changing BFF/runtime/canonical truth; downstream frontend guidance remains the merged followup-2 research.run.progress / queued-dispatch / ETag+Idempotency-Key handoff." \
  ./scripts/ai-status.sh approve INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR-SIDECAR-BFF-HANDOFF \
  "Support-only missing-PR BFF/frontend handoff packet approved for parent unblock owner."
```

Suggested reviewer command if changes are required:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen INTEGRATION-UNBLOCK-AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-MISSING-PR-SIDECAR-BFF-HANDOFF \
  "Describe the factual correction, missing PR evidence, or handoff boundary issue required before approval."
```
