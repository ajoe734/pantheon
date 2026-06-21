# INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-CI-RED BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Task ID | `INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-CI-RED-SIDECAR-BFF-HANDOFF` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-CI-RED` — second-level CI-red unblock for the CI-red unblock chain |
| Parent owner / reviewer | `Claude` / `Claude2` |
| Prepared by | `Claude2` |
| Reviewer | `Claude` |
| Date | 2026-06-21 |
| Mutates canonical truth | `false` |
| Status | Draft — ready for reviewer handoff |

This packet is a **support artifact only**. It does not modify L1 canonical truth,
OpenAPI, JSON schemas, BFF runtime, registry/governance implementation, or
execute-plans frontend code. It documents the resolution of the second-level
CI-red in the `INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-CI-RED`
unblock chain, records the current BFF query gap and operator journey state, and provides
forward handoff materials for the parent task closeout.

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates ownership; support packets do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/integration_unblock_integration_unblock_ag_be_cp_001_sidecar_bff_handoff_ci_red_ci_red_sidecar_bff_handoff.md` | Sidecar is support-only: prepare BFF handoff materials for the double CI-red unblock parent; not canonical truth. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support changes require explicit scope and narrow commit discipline. |
| `.orchestrator/skills/task-closeout-finalization.md` | Owner must not call `done` while the task PR is still open. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-CI-RED` | Parent: `review_approved`; owner `Claude`; reviewer `Claude2`; PR #2120 merged at `2026-06-21T17:38:32Z`; review notes confirm all CI checks passed (Commit trailers, Runtime mirror guard, Smoke acceptance). |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED` | Archived as `done`; terminal outcome `completed`; archived at `2026-06-21T17:32:39Z`; PR #2114 merged. |
| `gh pr list` for the unblock chain | PR #2120 merged; PR #2119 merged; PR #2118 merged; PR #2117 merged; PR #2116 merged; PR #2115 merged; PR #2114 merged; PR #2121 still OPEN (rebase-conflict sidecar brief). |
| Previous sidecar packets | `INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-SIDECAR-BFF-HANDOFF.md` (original; PR #2116 merged) and `...-FOLLOWUP-2.md` (PR #2119 merged) document the CI-red root cause and BFF gap matrix in detail. |
| `support/sidecars/AG-BE-CP-001/AG-BE-CP-001-SIDECAR-BFF-HANDOFF.md` | Original BFF handoff packet: CandidatePool/score/decision/discussion/monitoring BFF gap matrix, A2 recipe operator journeys A–F, TypeScript client signatures, and design notes. Status: `done`, approved by Codex. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## CI-Red Chain Resolution Summary

### Unblock Chain Overview

The CI-red resolution chain for `AG-BE-CP-001-SIDECAR-BFF-HANDOFF` involved two nested
integration-unblock tasks, both caused by the same **shallow-fetch git log exit-128 false
positive** on the Commit trailers CI status check:

| Level | Task | Root cause | Resolution | PR | State |
|---|---|---|---|---|---|
| Original | `AG-BE-CP-001-SIDECAR-BFF-HANDOFF` | BFF handoff packet content | Completed | #2109 merged | `done` |
| CI-red L1 | `INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED` | Shallow-fetch exit-128 false positive on Commit trailers | Fresh commit pushed; CI passed | #2114 merged | `done` |
| CI-red L2 | `INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-CI-RED` | Same shallow-fetch false positive on the CI-red unblock task itself | Resolution brief committed (d589cf9e); CI passed on PR #2120 | #2120 merged | `review_approved` |

### What The Second CI-Red Was

When the L1 integration-unblock task
(`INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED`) went through
its own closeout push, the auto-integrator detected CI still red and opened a
second-level unblock task. Root cause was the same infrastructure false positive:
the shallow-fetch clone depth used by the CI runner did not capture the `BASE_SHA`
in the push event, causing `git log <BASE_SHA>..<HEAD>` to exit 128.

### Resolution Applied

Owner Claude committed a resolution brief (`d589cf9e`) on top of the task branch.
PR #2120 (`task/INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-CI-RED`)
was opened. All three required CI checks passed (Commit trailers, Runtime mirror guard,
Smoke acceptance). PR #2120 merged into `dev` at `2026-06-21T17:38:32Z`.

Reviewer Claude2 approved the parent task with review notes confirming:
- Parent L1 task `INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED` is archived `done`.
- CI-red is the same shallow-fetch exit-128 false positive, correctly diagnosed and fixed.
- PR #2120 merged with all CI checks green.
- All three acceptance criteria met.
- Brief commit `d589cf9e` includes correct trailers.

## Current Task Lifecycle State

| Task | Status | Notes |
|---|---|---|
| `AG-BE-CP-001-SIDECAR-BFF-HANDOFF` | `done` / archived; PR #2109 merged | Original BFF handoff packet; terminal outcome `completed` |
| `INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED` | `done` / archived; PR #2114 merged at `2026-06-21T17:32:22Z` | L1 CI-red unblock; resolved and archived |
| `INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-CI-RED` | **`review_approved`**; owner Claude must finalize | L2 CI-red unblock; PR #2120 merged at `2026-06-21T17:38:32Z` |
| `AG-BE-CP-001` | `blocked`; owner `Codex`, reviewer `Claude2` | Main CandidatePool implementation; waiting on schema/route/lifecycle clarification |
| `AG-FE-TR-002` | `todo`; depends on `AG-BE-CP-001` | Frontend `CandidateReviewDrawer` and pool client; gated on `AG-BE-CP-001` routes |
| This sidecar | `in_progress` | BFF handoff support artifact only |

### PR Reference Table

| PR | Branch | State | Notes |
|---|---|---|---|
| #2109 | `task/AG-BE-CP-001-SIDECAR-BFF-HANDOFF` | MERGED | Original BFF handoff packet |
| #2114 | `task/INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED` | MERGED at `2026-06-21T17:32:22Z` | L1 CI-red unblock brief |
| #2115 | `task/INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT` | MERGED | Rebase-conflict unblock |
| #2116 | `task/INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-SIDECAR-BFF-HANDOFF` | MERGED at `2026-06-21T17:07:09Z` | L1 CI-red sidecar BFF handoff |
| #2117 | `task/INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-SIDECAR-BFF-HAND` | MERGED at `2026-06-21T17:15:57Z` | L2 CI-red sidecar brief |
| #2118 | `task/INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR-BFF-HANDOFF` | MERGED at `2026-06-21T17:22:07Z` | Rebase-conflict sidecar closeout |
| #2119 | `task/INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | MERGED at `2026-06-21T17:41:22Z` | L1 CI-red followup-2 BFF handoff |
| #2120 | `task/INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-CI-RED` | MERGED at `2026-06-21T17:38:32Z` | L2 CI-red unblock resolution brief |
| #2121 | `task/INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR-BFF-HANDOFF` | OPEN | Rebase-conflict sidecar finalize (separate chain) |

PR #2120 is the parent task branch. It is already merged. Claude may proceed with
`scripts/ai-status.sh done` once the closeout checklist is complete.

## BFF Query Gap Matrix (Unchanged)

The CI-red unblock chain (L1 and L2) did not add, remove, or change any BFF route.
The BFF gap matrix is carried forward from `support/sidecars/AG-BE-CP-001/AG-BE-CP-001-SIDECAR-BFF-HANDOFF.md`
and confirmed unchanged through the FOLLOWUP-2 packet (PR #2119):

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

`candidate_pool.schema.json` has `additionalProperties: false` at both pool and member level.
No score, discussion, monitoring, or negative-example fields can be added without explicit
design-team schema extension. `AG-BE-CP-001` owner (`Codex`) must not self-create these fields.

None of these routes create a broker order, `RuntimeBinding`, or capital binding.

### Three Active Blockers For AG-BE-CP-001 (Carried Forward)

1. **Schema extension** — `candidate_pool.schema.json` needs `candidate_score.schema.json`
   or an explicit extension for score, discussion, monitoring, and negative-example fields.
2. **Route definition** — The OpenAPI files have no candidate pool/score/decision route;
   §17.3 references the endpoint conceptually but does not provide the full HTTP path.
3. **Lifecycle-state transition map** — Decision recording (G-05) requires a canonical
   state-machine definition for candidate pool member lifecycle.

These blockers are unchanged from the FOLLOWUP-2 packet. The CI-red unblock chain did not
modify `AG-BE-CP-001` scope or its blocker state.

## Operator Journey Summary

The A2 scoring recipe and six operator journeys (A–F) are documented in the original
BFF handoff packet. No journey changed during the CI-red unblock chain.

| Journey | Description | Prerequisite routes |
|---|---|---|
| A | View available candidate pools | G-01 |
| B | Inspect pool members and their scores | G-02, G-03 |
| C | Trigger re-score for a specific candidate | G-04 |
| D | Record a committee decision (approve / reject / defer) | G-05 (lifecycle map required) |
| E | Add or read committee discussion threads | G-06, G-07 |
| F | View monitoring record for a candidate | G-08 |

All journeys remain blocked pending `AG-BE-CP-001` implementation and the three blockers above.

## Frontend Handoff State

| Frontend task | Status | Gate |
|---|---|---|
| `AG-FE-TR-002` — CandidateReviewDrawer + pool client | `todo` | Depends on `AG-BE-CP-001` |

The TypeScript client method signatures from the original BFF handoff packet remain the
authoritative interface spec. No frontend code change is needed until `AG-BE-CP-001` routes
land in `dev` and the three blockers are resolved.

## Parent Closeout Requirements For Claude

The parent task (`INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-CI-RED`)
is in `review_approved`. Per `.orchestrator/skills/task-closeout-finalization.md`:

1. **Verify PR #2120 merged** — Confirmed merged at `2026-06-21T17:38:32Z` (packet-time fact).
2. **Run closeout command** after verifying the above:
   ```bash
   AI_NAME=Claude ./scripts/ai-status.sh done \
     INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-CI-RED \
     "Finalized review_approved → done: PR #2120 merged; L2 CI-red shallow-fetch false positive resolved; INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED archived done; full CI-red chain resolved."
   ```
3. **Push after done**:
   ```bash
   git push
   ```

Claude must verify PR #2120 is still merged (not reverted) before calling `done`.
The `done` guard enforces that the task branch HEAD is an ancestor of `dev`.

## Support-Only Boundary

- This sidecar does not edit `AI_COLLABORATION_GUIDE.md`, `TARGET_ARCHITECTURE.md`, or any L1 policy file.
- This sidecar does not edit `scripts/git/auto_integrator.py` or any CI/CD workflow file.
- This sidecar does not edit runtime, registry, governance, BFF router, OpenAPI, or JSON schema files.
- This sidecar does not edit execute-plans frontend code.
- This sidecar does not approve or finalize the parent task.
- This sidecar records packet-time state; reviewer should re-verify PR #2120 merge before parent closeout.

## Evidence Commands Run

```bash
# Branch and worktree check
git branch --show-current
# task/INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-CI-RED-SIDECAR-BFF-HANDOFF

git status --short
# ?? .orchestrator/task-briefs/integration_unblock_integration_unblock_ag_be_cp_001_sidecar_bff_handoff_ci_red_ci_red_sidecar_bff_handoff.md

# Task status checks
AI_NAME=Claude2 python3 scripts/ai_status.py show INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-CI-RED
AI_NAME=Claude2 python3 scripts/ai_status.py show INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED

# PR list check
gh pr list --state all --search "INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001" \
  --json number,title,state,headRefName,mergedAt,url --limit 10
```

Observed facts used by this packet (as of 2026-06-21):

- Parent `INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-CI-RED` is `review_approved`; Claude2 approved; owner Claude must finalize.
- PR #2120 merged at `2026-06-21T17:38:32Z`; all CI checks green.
- L1 task `INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED` is `done` / archived.
- `AG-BE-CP-001` is still `blocked`; BFF gap matrix (G-01 through G-08) unchanged.
- Frontend task `AG-FE-TR-002` remains `todo`, gated on `AG-BE-CP-001`.
- PR #2121 (rebase-conflict sidecar brief finalization) is still OPEN; separate chain, not blocking parent closeout.

## Reviewer Handoff

Reviewer: `Claude`

Claude review should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact and task-owned status metadata are in scope. |
| Canonical truth | No canonical docs, schemas, OpenAPI, BFF runtime, registry/governance, or frontend files changed. |
| CI-red chain summary | L1 and L2 CI-red both correctly identified as the same shallow-fetch exit-128 false positive; PR #2120 confirmed merged. |
| State accuracy | Parent is `review_approved`; PR #2120 merged; L1 task `done`; `AG-BE-CP-001` blocked; `AG-FE-TR-002` todo. |
| BFF gap forward | Eight-route gap matrix and three blockers accurately reflect `AG-BE-CP-001` state and previous packets. |
| Closeout requirements | Claude's required steps are correctly stated; `done` guard will verify PR #2120 ancestry. |
| No-order guard | No candidate route creates a broker order, `RuntimeBinding`, or capital binding. |

Recommended reviewer approval command:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-CI-RED/INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-CI-RED-SIDECAR-BFF-HANDOFF.md \
  REVIEW_NOTES_ZH="BFF handoff packet approved: documents L2 CI-red chain resolution (shallow-fetch exit-128 false positive, same as L1), PR #2120 merged with all CI checks green, parent review_approved state with Claude closeout steps, 8-route BFF gap matrix unchanged, AG-BE-CP-001 still blocked on 3 schema/route/lifecycle items, support-only boundary maintained." \
  ./scripts/ai-status.sh approve INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-CI-RED-SIDECAR-BFF-HANDOFF \
  "BFF/frontend handoff packet for INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-CI-RED approved for parent owner absorption."
```

Recommended reviewer correction command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen INTEGRATION-UNBLOCK-INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-CI-RED-SIDECAR-BFF-HANDOFF \
  "Describe the factual correction, scope violation, or missing handoff detail needed before approval."
```

Prepared by Claude2 for the support-only BFF handoff sidecar.
