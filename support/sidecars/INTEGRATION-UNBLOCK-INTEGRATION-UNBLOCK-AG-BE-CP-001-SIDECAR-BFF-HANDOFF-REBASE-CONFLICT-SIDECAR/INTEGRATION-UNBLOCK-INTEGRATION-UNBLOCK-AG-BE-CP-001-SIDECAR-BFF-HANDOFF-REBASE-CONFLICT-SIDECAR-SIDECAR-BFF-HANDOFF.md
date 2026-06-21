# INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Task ID | `INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR-SIDECAR-BFF-HANDOFF` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR` — ci-red integration unblock for the REBASE-CONFLICT-SIDECAR-BFF-HANDOFF task |
| Parent owner / reviewer | `Claude2` / `Claude` |
| Prepared by | `Claude` |
| Reviewer | `Claude2` |
| Date | 2026-06-21 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |

This packet is a **support artifact only**. It does not modify L1 canonical truth,
OpenAPI, JSON schemas, BFF runtime, registry/governance implementation, or
execute-plans frontend code. It documents the resolution of the ci-red integration
unblock for `INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR-BFF-HANDOFF`,
records the current BFF query gap and operator journey state, and provides forward
handoff materials for the parent task closeout.

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates ownership; support packets do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/integration_unblock_integration_unblock_ag_be_cp_001_sidecar_bff_handoff_rebase_conflict_sidecar_sidecar_bff_handoff.md` | Sidecar is support-only: prepare BFF handoff materials for the REBASE-CONFLICT-SIDECAR ci-red unblock parent; not canonical truth. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support changes require explicit scope and narrow commit discipline. |
| `.orchestrator/skills/task-closeout-finalization.md` | Owner must not call `done` while the task PR is still open; `done` guard verifies task branch HEAD is ancestor of `dev`. |
| `AI_NAME=Claude python3 scripts/ai_status.py show INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR` | Parent: `in_progress`; owner `Claude2`; reviewer `Claude`; next: "Claude2 investigating: dependent task INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR-BFF-HANDOFF is already archived as done (PR #2121 merged to dev, CI green). Documenting resolution and closing out unblock task." |
| `AI_NAME=Claude python3 scripts/ai_status.py show INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR-BFF-HANDOFF` | Archived as `done`; terminal outcome `completed`; archived at `2026-06-21T18:00:54Z`; PR #2121 merged to dev; commit `1f5ebacf`; all CI checks green. |
| `support/sidecars/AG-BE-CP-001/AG-BE-CP-001-SIDECAR-BFF-HANDOFF.md` | Original authoritative BFF handoff packet: 8-route gap matrix, A2 recipe operator journeys A–F, TypeScript client signatures, 3 active blockers. Status `done`, PR #2109 merged to dev. |
| `support/sidecars/INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT/INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR-BFF-HANDOFF.md` | Prior REBASE-CONFLICT sidecar BFF packet (done, PR #2121). Confirmed: original packet in dev (PR #2109), rebase conflict resolved, AG-BE-CP-001 still blocked on 3 design deliverables, no-order guard and Trading Room isolation correctly stated. |
| `support/sidecars/INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-CI-RED/INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-CI-RED-SIDECAR-BFF-HANDOFF.md` | CI-RED-CI-RED sidecar packet (done, PR #2123). Documents L2 CI-red shallow-fetch exit-128 false positive root cause; 8-route gap matrix (G-01 through G-08) and 3 blockers confirmed unchanged. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## CI-Red Integration Unblock Resolution Summary

### Context

The parent task
(`INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR`)
is a ci-red integration unblock that was auto-created for
`INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR-BFF-HANDOFF`.

Claude2's investigation determined the dependent task is already archived as `done`:
- PR #2121 merged to `dev` (commit `1f5ebacf`)
- Archive timestamp: `2026-06-21T18:00:54Z`
- All CI checks passed: Commit trailers, Runtime mirror guard, Smoke acceptance

The ci-red trigger was an auto-integrator observation that occurred before the PR
merged and CI completed. The underlying task completed successfully; the unblock
task is a no-op close-out.

### Resolution Type

This is a **resolved-before-investigation** case: the auto-integrator raised the
ci-red unblock, but by the time Claude2 investigated, the dependent task PR #2121
had already merged cleanly to `dev` with all CI checks green. No fix or retry was
required. The parent task only needs documentation and close-out.

### Complete PR Chain For This Sidecar Family

| PR | Branch | State | Notes |
|---|---|---|---|
| #2109 | `task/AG-BE-CP-001-SIDECAR-BFF-HANDOFF` | MERGED | Original BFF handoff packet for AG-BE-CP-001 |
| #2114 | `task/INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED` | MERGED | L1 CI-red unblock (shallow-fetch false positive) |
| #2115 | `task/INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT` | MERGED | Rebase-conflict unblock brief |
| #2116 | `task/INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-SIDECAR-BFF-HANDOFF` | MERGED | L1 CI-red sidecar BFF handoff |
| #2117 | `task/INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-SIDECAR-BFF-HAND` | MERGED | L2 CI-red sidecar brief (truncated branch name) |
| #2118 | `task/INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR-BFF-HANDOFF` | MERGED | Rebase-conflict sidecar closeout commit |
| #2119 | `task/INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | MERGED | L1 CI-red sidecar FOLLOWUP-2 BFF handoff |
| #2120 | `task/INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-CI-RED` | MERGED | L2 CI-red unblock resolution brief |
| #2121 | `task/INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR-BFF-HANDOFF` | MERGED | REBASE-CONFLICT-SIDECAR-BFF-HANDOFF sidecar finalize |
| #2123 | `task/INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-CI-RED-SIDECAR-BFF-HANDOFF` | MERGED | CI-RED-CI-RED sidecar BFF handoff |

Parent task `INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR`
does not yet have a merged PR (Claude2 is closing it out); this sidecar does not block that close-out.

## Current Task Lifecycle State

| Task | Status | Notes |
|---|---|---|
| `AG-BE-CP-001-SIDECAR-BFF-HANDOFF` | `done` / archived; PR #2109 merged | Original BFF handoff packet; terminal outcome `completed` |
| `INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED` | `done` / archived; PR #2114 merged at `2026-06-21T17:32:22Z` | L1 CI-red unblock; resolved and archived |
| `INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT` | `done` / archived; PR #2115 merged | Rebase-conflict unblock; resolved and archived |
| `INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-CI-RED` | `done` / archived; PR #2120 merged at `2026-06-21T17:38:32Z` | L2 CI-red unblock; resolved and archived |
| `INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR-BFF-HANDOFF` | `done` / **archived** at `2026-06-21T18:00:54Z`; PR #2121 merged | REBASE-CONFLICT-SIDECAR-BFF-HANDOFF packet; resolved and archived |
| `INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR` | `in_progress`; owner `Claude2` | **Parent task** — ci-red unblock for above; resolved-before-investigation; Claude2 closing out |
| `AG-BE-CP-001` | `blocked`; owner `Codex`, reviewer `Claude2` | Main CandidatePool implementation; waiting on schema/route/lifecycle clarification from design |
| `AG-FE-TR-002` | `todo`; depends on `AG-BE-CP-001` | Frontend `CandidateReviewDrawer` and pool client; gated on `AG-BE-CP-001` routes |
| This sidecar | `in_progress` → handoff | BFF handoff support artifact only |

## BFF Query Gap Matrix (Unchanged)

The entire unblock chain for `AG-BE-CP-001-SIDECAR-BFF-HANDOFF` — including the
rebase-conflict resolution, all CI-red levels, and their sidecar packets — did not
add, remove, or change any BFF route. The BFF gap matrix is carried forward from
`support/sidecars/AG-BE-CP-001/AG-BE-CP-001-SIDECAR-BFF-HANDOFF.md` and confirmed
unchanged through all prior sidecar packets:

| Gap # | Route | Method | Description | Status | Blocked by |
|---|---|---|---|---|---|
| G-01 | `/bff/agora/candidate-pools` | `GET` | List candidate pools | Not implemented | `AG-BE-CP-001` primary |
| G-02 | `/bff/agora/candidate-pools/{pool_id}` | `GET` | Candidate pool detail | Not implemented | `AG-BE-CP-001` primary |
| G-03 | `/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/score` | `GET` | Candidate score detail | Not implemented | `candidate_score.schema.json` undefined |
| G-04 | `/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/score` | `POST` | Candidate re-score trigger | Not implemented | Schema + route definition missing |
| G-05 | `/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/decision` | `POST` | Candidate decision recording | Not implemented | Lifecycle-state transition map missing |
| G-06 | `/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/discussions` | `GET` | Candidate discussion list | Not implemented | Schema extension required |
| G-07 | `/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/discussions` | `POST` | Candidate discussion create | Not implemented | Schema extension required |
| G-08 | `/bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/monitoring` | `GET` | Candidate monitoring record | Not implemented | Schema extension required |

`candidate_pool.schema.json` has `additionalProperties: false` at both pool and member
level. No score, discussion, monitoring, or negative-example fields can be added without
explicit design-team schema extension. `AG-BE-CP-001` owner (`Codex`) must not
self-create these fields.

None of these routes create a broker order, `RuntimeBinding`, or capital binding.

### Three Active Blockers For AG-BE-CP-001 (Carried Forward)

1. **Schema extension** — `candidate_pool.schema.json` needs `candidate_score.schema.json`
   or an explicit extension for score, discussion, monitoring, and negative-example fields.
2. **Route definition** — The OpenAPI files have no candidate pool/score/decision route;
   §17.3 references the endpoint conceptually but does not provide the full HTTP path.
3. **Lifecycle-state transition map** — Decision recording (G-05) requires a canonical
   state-machine definition for candidate pool member lifecycle.

These blockers are unchanged from all prior sidecar packets. The rebase-conflict and
ci-red unblock chain did not modify `AG-BE-CP-001` scope or its blocker state.

## Operator Journey Summary

The A2 scoring recipe and six operator journeys (A–F) are documented in the original
BFF handoff packet. No journey changed during any unblock in this chain.

| Journey | Description | Prerequisite routes |
|---|---|---|
| A | View available candidate pools | G-01 |
| B | Inspect pool members and their scores | G-02, G-03 |
| C | Trigger re-score for a specific candidate | G-04 |
| D | Record a committee decision (approve / reject / defer) | G-05 (lifecycle map required) |
| E | Add or read committee discussion threads | G-06, G-07 |
| F | View monitoring record for a candidate | G-08 |

All journeys remain blocked pending `AG-BE-CP-001` implementation and the three
blockers above.

## Frontend Handoff State

| Frontend task | Status | Gate |
|---|---|---|
| `AG-FE-TR-002` — CandidateReviewDrawer + pool client | `todo` | Depends on `AG-BE-CP-001` |

The TypeScript client method signatures from the original BFF handoff packet remain
the authoritative interface spec. No frontend code change is needed until
`AG-BE-CP-001` routes land in `dev` and the three blockers are resolved.

Authoritative reference: `support/sidecars/AG-BE-CP-001/AG-BE-CP-001-SIDECAR-BFF-HANDOFF.md`
(in `dev` via PR #2109).

## Parent Closeout Requirements For Claude2

The parent task (`INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR`)
is `in_progress`. Per `.orchestrator/skills/task-closeout-finalization.md`,
Claude2 must complete the closeout sequence:

1. **Verify PR #2121 merged** — The dependency
   (`INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR-BFF-HANDOFF`)
   is already archived as `done` (PR #2121 merged to `dev`, CI green, commit `1f5ebacf`,
   archived at `2026-06-21T18:00:54Z`). The ci-red was resolved before investigation
   was required.
2. **Create a task-scoped commit** on the parent task branch documenting the resolution.
3. **Push and open PR** via `./scripts/git/task_finalize.sh INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR`.
4. **Wait for the parent task PR to merge** into `dev`.
5. **Run done** after the parent PR merges:
   ```bash
   AI_NAME=Claude2 ./scripts/ai-status.sh done \
     INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR \
     "Finalized: dependent INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR-BFF-HANDOFF was already archived done (PR #2121 merged, CI green) before investigation; ci-red unblock is a resolved-before-investigation close-out."
   ```
6. **Push** after `done`.

Claude2 must verify PR #2121 is still merged (not reverted) before calling `done`.
The `done` guard enforces that the task branch HEAD is an ancestor of `dev`.

## Support-Only Boundary

- This sidecar does not edit `AI_COLLABORATION_GUIDE.md`, `TARGET_ARCHITECTURE.md`, or any L1 policy file.
- This sidecar does not edit `scripts/git/auto_integrator.py` or any CI/CD workflow file.
- This sidecar does not edit runtime, registry, governance, BFF router, OpenAPI, or JSON schema files.
- This sidecar does not edit execute-plans frontend code.
- This sidecar does not approve or finalize the parent task.
- This sidecar records packet-time state; reviewer should re-verify PR #2121 and parent task PR merge status before parent closeout.

## Evidence Commands Run

```bash
# Branch and worktree check
git branch --show-current
# task/INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR-SIDECAR-BFF-HANDOFF

git status --short
# ?? .orchestrator/task-briefs/integration_unblock_integration_unblock_ag_be_cp_001_sidecar_bff_handoff_rebase_conflict_sidecar_sidecar_bff_handoff.md

# Task status checks
AI_NAME=Claude python3 scripts/ai_status.py show INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR
# in_progress; Claude2 investigating; next: "dependent task already archived done (PR #2121 merged to dev, CI green)"

AI_NAME=Claude python3 scripts/ai_status.py show INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR-BFF-HANDOFF
# source: archive; terminal_status: done; archived_at: 2026-06-21T18:00:54Z; PR #2121 merged
```

Observed facts used by this packet (as of 2026-06-21):

- Parent `INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR` is `in_progress`; Claude2 is the owner, Claude is the reviewer; Claude2 is closing out.
- Dependency `INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR-BFF-HANDOFF` is `done` / archived (PR #2121 merged to `dev`, CI green, commit `1f5ebacf`, archived at `2026-06-21T18:00:54Z`).
- `AG-BE-CP-001` is still `blocked`; BFF gap matrix (G-01 through G-08) unchanged.
- Frontend task `AG-FE-TR-002` remains `todo`, gated on `AG-BE-CP-001`.
- No candidate pool BFF route exists in `agora_v1.openapi.yaml` or `agora_v1_3.openapi.yaml`.

## Reviewer Handoff

Reviewer: `Claude2`

Claude2 review should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact and task-owned status metadata are in scope. |
| Canonical truth | No canonical docs, schemas, OpenAPI, BFF runtime, registry/governance, or frontend files changed. |
| Resolution type | Correctly identified as "resolved-before-investigation": PR #2121 was already merged and the dependency archived before Claude2 began investigating. |
| State accuracy | Dependency `INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR-BFF-HANDOFF` archived `done`; PR #2121 merged; `AG-BE-CP-001` blocked; `AG-FE-TR-002` todo. |
| BFF gap forward | Eight-route gap matrix and three blockers accurately reflect `AG-BE-CP-001` state and all previous packets. |
| Closeout requirements | Claude2's required closeout steps are correctly stated; `done` guard will verify PR ancestry against `dev`. |
| No-order guard | No candidate route creates a broker order, `RuntimeBinding`, or capital binding. |
| Consumer guidance | Consumers correctly directed to the canonical packet at `support/sidecars/AG-BE-CP-001/AG-BE-CP-001-SIDECAR-BFF-HANDOFF.md` in `dev`; frontend gated on `AG-BE-CP-001` routes; Trading Room isolation boundary preserved. |

Recommended reviewer approval command:

```bash
AI_NAME=Claude2 REVIEW_FILE=support/sidecars/INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR/INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR-SIDECAR-BFF-HANDOFF.md \
  REVIEW_NOTES_ZH="Support-only BFF/frontend handoff packet approved: correctly identifies the ci-red unblock as resolved-before-investigation (PR #2121 merged, dependency archived done), 8-route BFF gap matrix unchanged, 3 blockers for AG-BE-CP-001 correctly stated, parent closeout requirements for Claude2 spelled out, no canonical truth changes, no-order guard and consumer guidance maintained." \
  ./scripts/ai-status.sh approve INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR-SIDECAR-BFF-HANDOFF \
  "BFF/frontend handoff packet for INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR approved for parent owner absorption."
```

Recommended reviewer correction command:

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh reopen INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR-SIDECAR-BFF-HANDOFF \
  "Describe the factual correction, scope violation, or missing handoff detail needed before approval."
```

Prepared by Claude for the support-only BFF handoff sidecar.
