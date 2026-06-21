# INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Task ID | `INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-SIDECAR-BFF-HANDOFF` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED` — CI-red unblock for AG-BE-CP-001-SIDECAR-BFF-HANDOFF |
| Parent owner / reviewer | `Claude` / `Codex` |
| Prepared by | `Claude2` |
| Reviewer | `Claude` |
| Date | 2026-06-21 |
| Mutates canonical truth | `false` |
| Status | Approved — ready for parent owner absorption |

This packet is a support artifact only. It does not modify L1 canonical truth,
OpenAPI, JSON schemas, BFF runtime, registry/governance implementation, or
execute-plans frontend code. It summarizes the CI-red root cause, the resolution
path, the current state of related tasks, and the forward handoff boundary for
`INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED`.

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates ownership and lifecycle; support packets do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/integration_unblock_ag_be_cp_001_sidecar_bff_handoff_ci_red_sidecar_bff_handoff.md` | Sidecar is support-only: prepare BFF handoff materials for the CI-red unblock parent, not canonical truth. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support changes require explicit scope and narrow commit discipline. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo changes must pass task commit, PR, merge, and owner closeout before `done`. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED` | Parent: `in_progress`, owner `Claude`, reviewer `Codex`, depends on `AG-BE-CP-001-SIDECAR-BFF-HANDOFF`. Next note: "Root cause identified: Commit trailers push-event false positive (git log exit 128 on shallow fetch range). Fix: fresh commit pushed on top. PR #2109 merged successfully. All acceptance criteria met. Task brief written. Preparing commit and handoff." |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-BE-CP-001-SIDECAR-BFF-HANDOFF` | Archived as `done`; PR #2109 merged into `dev` at `2026-06-21T16:41:13Z`; delivery branch `task/AG-BE-CP-001-SIDECAR-BFF-HANDOFF`; HEAD merged to target; push status `in_sync`. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-BE-CP-001` | Active, `blocked`; owner `Codex`, reviewer `Claude2`; depends on `AG-BE-RS-002` (done); blocked on: missing candidate score/review HTTP route, missing schema extension (`candidate_pool.schema.json` has `additionalProperties: false`), and missing lifecycle-state transition map. |
| `gh pr list` for AG-BE-CP-001-SIDECAR-BFF-HANDOFF | PR #2109 (`task/AG-BE-CP-001-SIDECAR-BFF-HANDOFF`) merged at `2026-06-21T16:41:13Z`. PR #2114 (`task/INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED`) open ("add brief"). |
| `python3 scripts/git/auto_integrator.py --task-id INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED --json --no-lock` | `candidate_count: 0`; no pending integration candidates for the CI-red unblock task. |
| `support/sidecars/AG-BE-CP-001/AG-BE-CP-001-SIDECAR-BFF-HANDOFF.md` | The original BFF handoff packet (status: done, approved by Codex). Contains CandidatePool/score/decision/discussion/monitoring BFF gap matrix, A2 recipe operator journeys A–F, TypeScript client method signatures, acceptance checks, and four open design notes. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## CI-Red Root Cause And Resolution

### What Happened

The `AG-BE-CP-001-SIDECAR-BFF-HANDOFF` task branch (`task/AG-BE-CP-001-SIDECAR-BFF-HANDOFF`)
had commits that satisfied the content requirements but failed the **Commit trailers** required
CI status check. The failure was a false positive caused by a `git log` exit-128 error on a
shallow fetch range. The shallow-clone depth used by the CI runner did not contain the full
ancestry required by the trailers check's `git log` invocation, so the check reported failure
even though the trailers were correctly formatted.

The integration-unblock task (`INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED`)
was auto-created by the auto-integrator when it detected that CI was red and the `review_approved`
task was stranded.

### Fix Applied

Owner Claude identified the root cause and pushed a fresh commit on top of the existing branch.
The fresh push triggers a new CI run from the current HEAD, which succeeds because the shallow
fetch now captures a range that includes the relevant commit boundary. After the fresh push, the
Commit trailers check passed on PR #2109.

### Current State After Fix

| Artifact | State |
|---|---|
| PR #2109 (`task/AG-BE-CP-001-SIDECAR-BFF-HANDOFF`) | Merged into `dev` at `2026-06-21T16:41:13Z` |
| `AG-BE-CP-001-SIDECAR-BFF-HANDOFF` task | Archived as `done`; terminal outcome `completed` |
| `INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED` task | `in_progress`; PR #2114 open to add the task brief / root-cause note |
| Auto-integrator candidate queue for parent CI-red task | `candidate_count: 0`; no stranded candidates remain |

## Dependency Map

| Task | Status | Implication |
|---|---|---|
| `AG-BE-RS-002` | `done` / archived (PR #2092 merged; closeout merge `3566d9e6`) | The `run_ref` field linking candidate members to research-run projections is available. |
| `AG-BE-CP-001-SIDECAR-BFF-HANDOFF` | `done` / archived (PR #2109 merged) | Original BFF handoff packet is complete and absorbed into `dev`. |
| `AG-BE-CP-001` | `blocked`; owner `Codex`, reviewer `Claude2` | Main CandidatePool/Member/Discussion/Monitoring implementation remains blocked on schema/route/lifecycle clarification. The sidecar BFF packet was the only support artifact; main implementation is still awaited. |
| `AG-FE-TR-002` | `todo`; depends on `AG-BE-CP-001` | Frontend `CandidateReviewDrawer` and pool client still gated on `AG-BE-CP-001` routes landing. |
| `INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED` | `in_progress`; PR #2114 open | Parent CI-red unblock task; this sidecar supports it. |
| This sidecar | `in_progress` | Provides BFF handoff and dependency context only; does not finalize parent lifecycle. |

## BFF Forward Handoff

The CI-red was in the sidecar support task, not in the main `AG-BE-CP-001` implementation. The
original BFF handoff packet at `support/sidecars/AG-BE-CP-001/AG-BE-CP-001-SIDECAR-BFF-HANDOFF.md`
is complete and accurate as of its reviewed state. The following gaps still apply for the parent
implementation work:

| Gap | Needed surface | Status |
|---|---|---|
| Candidate pool list | `GET /bff/agora/candidate-pools` | Not implemented; `AG-BE-CP-001` primary |
| Candidate pool detail | `GET /bff/agora/candidate-pools/{pool_id}` | Not implemented; `AG-BE-CP-001` primary |
| Candidate score detail | `GET /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/score` | Not implemented; blocked on `candidate_score.schema.json` |
| Candidate re-score trigger | `POST /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/score` | Not implemented; blocked on schema and route definition |
| Candidate decision recording | `POST /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/decision` | Not implemented; blocked on lifecycle-state transition map |
| Candidate discussion list/create | `GET` / `POST /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/discussions` | Not implemented; blocked on schema extension |
| Candidate monitoring record | `GET /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/monitoring` | Not implemented; blocked on schema extension |
| Candidate score JSON Schema | `candidate_score.schema.json` (or extension to `candidate_pool.schema.json`) | Not created; design-team deliverable |

The `candidate_pool.schema.json` has `additionalProperties: false` at both pool and member level.
No score, discussion, monitoring, or negative-example fields can be added without explicit
schema extension from the design team. `AG-BE-CP-001` owner (`Codex`) must not self-create these
fields.

### CI-Red Handoff Note For Operator Tooling

This CI-red was a CI infrastructure false positive in a support-only sidecar task branch. It has
no effect on the operator-facing BFF surface, the candidate pool query contract, the A2 scoring
recipe, or the TypeScript client API. No BFF route, OpenAPI spec, JSON Schema, or execute-plans
code changed as a result of this unblock.

Operators building against the candidate pool BFF surface should continue to follow the
`AG-BE-CP-001-SIDECAR-BFF-HANDOFF.md` handoff packet, which documents the definitive BFF gap
matrix, A2 recipe operator journeys (A–F), and frontend client signatures once `AG-BE-CP-001`
routes land.

## Parent Acceptance Checklist

| # | Parent acceptance criterion | Sidecar assessment | Evidence |
|---|---|---|---|
| A1 | Root cause for `AG-BE-CP-001-SIDECAR-BFF-HANDOFF` integration blocker is documented | READY FOR REVIEW | Parent next note states: "Commit trailers push-event false positive (git log exit 128 on shallow fetch range)." PR #2114 adds the task brief with root-cause note. |
| A2 | Original PR is updated or superseded | MET | PR #2109 merged at `2026-06-21T16:41:13Z`; `AG-BE-CP-001-SIDECAR-BFF-HANDOFF` is archived as `done`. |
| A3 | Task no longer strands in `review_approved` | MET | `AG-BE-CP-001-SIDECAR-BFF-HANDOFF` is `done`; the original `review_approved` stranding that triggered the auto-integrator is resolved. |
| A4 | No canonical or runtime implementation surface changed by this sidecar | MET | Sidecar output is limited to this support artifact; no L1, OpenAPI, schema, runtime, or frontend files changed. |

Reviewer timing note: PR #2114 (`INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED: add brief`)
was open at packet-preparation time. Parent approval should re-check PR #2114 state before
moving parent to `review_approved` → `done`.

## Evidence Commands Run

Commands run from the sidecar task worktree:

```bash
git branch --show-current
# task/INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-SIDECAR-BFF-HANDOFF

git status --short
# ?? .orchestrator/task-briefs/integration_unblock_ag_be_cp_001_sidecar_bff_handoff_ci_red_sidecar_bff_handoff.md

AI_NAME=Claude2 python3 scripts/ai_status.py show INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-BE-CP-001-SIDECAR-BFF-HANDOFF
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-BE-CP-001

gh pr list --state all --search "AG-BE-CP-001-SIDECAR-BFF-HANDOFF" \
  --json number,title,state,headRefName,baseRefName,mergedAt,url --limit 10

python3 scripts/git/auto_integrator.py \
  --task-id INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED \
  --json --no-lock
```

Observed facts used by this packet:

- PR #2109 (`task/AG-BE-CP-001-SIDECAR-BFF-HANDOFF`) merged at `2026-06-21T16:41:13Z`.
- PR #2114 (`task/INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED`) open at packet time.
- `AG-BE-CP-001-SIDECAR-BFF-HANDOFF` is archived as `done`.
- Auto-integrator returned `candidate_count: 0` for the parent CI-red unblock task.
- `AG-BE-CP-001` is still `blocked` on schema/route/lifecycle definition.

## Support-Only Boundary

- This sidecar does not edit canonical documents (`AI_COLLABORATION_GUIDE.md`, `TARGET_ARCHITECTURE.md`, or any L1 policy file).
- This sidecar does not edit `scripts/git/auto_integrator.py` or any CI/CD workflow file.
- This sidecar does not edit runtime, registry, governance, BFF router, OpenAPI, or JSON schema files.
- This sidecar does not edit execute-plans frontend code.
- This sidecar does not approve or finalize the parent task.
- This sidecar does not claim PR #2114 is merged; it records the packet-time state and asks the reviewer to re-check before parent approval.

## Reviewer Handoff

Reviewer: `Claude`

Claude review should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact and task-owned status metadata are in scope. |
| Canonical truth | No canonical docs, schemas, OpenAPI, BFF runtime, registry/governance, or frontend files changed by this sidecar. |
| CI-red root cause | Correctly identified as shallow-fetch git log exit 128 on the Commit trailers CI check; fix was a fresh commit push resolving the CI run on PR #2109. |
| PR state | PR #2109 is confirmed merged; PR #2114 is noted as open at packet time; reviewer should re-check before parent approval. |
| Task state accuracy | `AG-BE-CP-001-SIDECAR-BFF-HANDOFF` is `done`; `AG-BE-CP-001` is still `blocked`; `AG-FE-TR-002` is `todo`. |
| BFF gap forward | The 8-route BFF gap matrix and 3 remaining blockers (schema extension, §17.3 route definition, lifecycle-state transition map) accurately reflect the `AG-BE-CP-001` blocker note and original handoff packet. |
| No-order guard | No candidate route in the gap matrix creates a broker order, `RuntimeBinding`, or capital binding. |

Recommended reviewer approval command:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED/INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-SIDECAR-BFF-HANDOFF.md \
  REVIEW_NOTES_ZH="Support-only BFF handoff packet approved: documents CI-red root cause (shallow-fetch git log exit 128 false positive on Commit trailers check), PR #2109 merged resolution, dependency chain (AG-BE-RS-002 done, AG-BE-CP-001-SIDECAR-BFF-HANDOFF done, AG-BE-CP-001 still blocked), 8-route BFF gap forward, and no-order-route boundary without modifying canonical truth or runtime files." \
  ./scripts/ai-status.sh approve INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-SIDECAR-BFF-HANDOFF \
  "Support-only BFF/frontend handoff packet for INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED approved for parent owner absorption."
```

Recommended reviewer correction command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-SIDECAR-BFF-HANDOFF \
  "Describe the factual correction, scope violation, or missing handoff detail needed before approval."
```

Prepared by Claude2 for the support-only BFF handoff sidecar.
