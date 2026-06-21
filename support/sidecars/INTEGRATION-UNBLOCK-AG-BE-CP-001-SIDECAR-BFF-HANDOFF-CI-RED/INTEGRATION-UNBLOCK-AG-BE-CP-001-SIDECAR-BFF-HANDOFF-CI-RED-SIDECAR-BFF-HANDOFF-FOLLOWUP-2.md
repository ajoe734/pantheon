# INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED — BFF & Frontend Handoff Packet (Followup-2)

| Field | Value |
|---|---|
| Task ID | `INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED` |
| Parent owner / reviewer | `Claude` / `Claude2` |
| Prepared by | `Claude2` |
| Reviewer | `Claude` |
| Date | 2026-06-21 |
| Mutates canonical truth | `false` |
| Supersedes | `INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-SIDECAR-BFF-HANDOFF.md` (previous sidecar, now merged as PR #2116) |

This packet is a **support artifact only**. It does not modify L1 canonical truth,
OpenAPI, JSON schemas, BFF runtime, registry/governance implementation, or
execute-plans frontend code. It provides an updated BFF query gap summary, operator
journey, and frontend handoff state reflecting the current task lifecycle as of
2026-06-21, after the previous sidecar was approved and merged.

## Current Task Lifecycle State

| Task | Status | Last updated |
|---|---|---|
| `AG-BE-CP-001-SIDECAR-BFF-HANDOFF` | `done` / archived; PR #2109 merged into `dev` at `2026-06-21T16:41:13Z` | 2026-06-21 |
| `INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED` | **`review_approved`**; reviewer Claude2 approved; owner Claude must finalize | 2026-06-21T17:15:48Z |
| `INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-SIDECAR-BFF-HANDOFF` | `done`; PR #2116 merged at `2026-06-21T17:07:09Z` | 2026-06-21 |
| `AG-BE-CP-001` | `blocked`; owner `Codex`, reviewer `Claude2`; waiting on schema/route/lifecycle clarification | 2026-06-21T15:10:07Z |
| This sidecar (Followup-2) | `in_progress`; preparing updated BFF and frontend handoff materials | 2026-06-21 |

### Open PRs Relevant To Parent Task

| PR | Branch | State | Notes |
|---|---|---|---|
| #2114 | `task/INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED` | OPEN | "add brief"; parent CI-red unblock task branch; must merge before Claude calls `done` |
| #2115 | `task/INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT` | OPEN | Rebase-conflict sidecar for the same parent group |
| #2118 | `task/INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR-BFF-HANDOFF` | OPEN | Finalize closeout for the rebase-conflict sidecar |

PR #2116 (`SIDECAR-BFF-HANDOFF`) is already merged. Parent closeout requires PR #2114 to
merge into `dev` before Claude may run `scripts/ai-status.sh done`.

## CI-Red Resolution Summary (from Previous Sidecar)

The `INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED` unblock was triggered by
a **shallow-fetch git log exit-128 false positive** on the Commit trailers CI status check.
The root cause and resolution were fully documented in the previous sidecar packet
(`SIDECAR-BFF-HANDOFF.md`, merged PR #2116). Key facts:

- PR #2109 (`task/AG-BE-CP-001-SIDECAR-BFF-HANDOFF`) was fixed by pushing a fresh commit on
  top; Commit trailers CI check passed; PR merged at `2026-06-21T16:41:13Z`.
- `AG-BE-CP-001-SIDECAR-BFF-HANDOFF` is archived `done`; no content failure; CI false positive
  only.
- Claude2 reviewed PR #2114 ("add brief") and approved the parent task with the note:
  `"審查通過：CI 全綠（PR #2114）、PR #2109 已合併至 dev、root cause 正確診斷並修復、brief 完整"`.

This followup packet does not re-litigate the CI root cause. The CI issue is resolved.
What remains is parent task closeout and the downstream BFF gap state.

## BFF Query Gap Matrix (Updated)

The original BFF gap matrix from `support/sidecars/AG-BE-CP-001/AG-BE-CP-001-SIDECAR-BFF-HANDOFF.md`
remains accurate. The CI-red and its resolution did not add, remove, or change any BFF
route. The following table reflects the gap state as of this followup packet:

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
No score, discussion, monitoring, or negative-example fields can be added to the schema
without explicit design-team approval. `AG-BE-CP-001` owner (`Codex`) must not self-create
these fields and must open a blocker for clarification.

None of these routes create a broker order, `RuntimeBinding`, or capital binding. All are
read-only or agora-internal write surfaces only.

### Three Active Blockers For AG-BE-CP-001

1. **Schema extension** — `candidate_pool.schema.json` needs `candidate_score.schema.json`
   or an explicit extension for score, discussion, monitoring, and negative-example fields.
   `AG-BE-CP-001` waits for design-team deliverable.
2. **Route definition** — The OpenAPI files (`agora_v1.openapi.yaml`, `agora_v1_3.openapi.yaml`)
   have no candidate pool/score/decision route. §17.3 (referenced in the task brief) names the
   endpoint conceptually but does not provide the full HTTP path in the canonical files.
3. **Lifecycle-state transition map** — Decision recording (G-05) requires a canonical
   state-machine definition for candidate pool member lifecycle (candidate → under-review →
   approved | rejected | deferred). This map is not in the current canonical spec.

Reviewer `Claude2` owns the blocker resolution decision for `AG-BE-CP-001` (the task lists
`waiting_for: Claude2`). This followup sidecar does not resolve those blockers — it records
them for situational awareness.

## Operator Journey Summary (A2 Recipe)

The A2 scoring recipe and the six operator journeys (A–F) are documented in the original
BFF handoff packet (`support/sidecars/AG-BE-CP-001/AG-BE-CP-001-SIDECAR-BFF-HANDOFF.md`).
No operator journey changed as a result of the CI-red resolution. The journeys remain:

| Journey | Description | Prerequisite routes |
|---|---|---|
| A | View available candidate pools | G-01 |
| B | Inspect pool members and their scores | G-02, G-03 |
| C | Trigger re-score for a specific candidate | G-04 |
| D | Record a committee decision (approve / reject / defer) | G-05 (lifecycle map required) |
| E | Add or read committee discussion threads | G-06, G-07 |
| F | View monitoring record for a candidate | G-08 |

Journeys A–F are blocked pending `AG-BE-CP-001` implementation and the three blockers above.
Frontend work on `AG-FE-TR-002` (`CandidateReviewDrawer` and pool client) is gated on these
routes landing in `dev`.

## Frontend Handoff State

| Frontend task | Status | Gate |
|---|---|---|
| `AG-FE-TR-002` — CandidateReviewDrawer + pool client | `todo` | Depends on `AG-BE-CP-001` |

The TypeScript client method signatures from the original BFF handoff packet remain the
authoritative interface spec. No frontend code change is needed until `AG-BE-CP-001` routes
land and the blockers above are resolved.

Frontend team should watch `AG-BE-CP-001` status for unblock. When `AG-BE-CP-001` moves to
`in_progress → review_approved → done`, all eight BFF routes should become available under
the existing BFF router at `services/control-plane/bff/agora/research.py`.

## Parent Closeout Requirements For Claude

The parent task (`INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED`) is in
`review_approved`. Claude must:

1. Verify PR #2114 (`task/INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED`)
   has merged into `dev`. If it is still open, check CI status and push a fix if needed.
2. After PR #2114 merges, run:
   ```bash
   AI_NAME=Claude ./scripts/ai-status.sh done \
     INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED \
     "Finalized review_approved → done: PR #2114 merged; CI-red root cause documented; AG-BE-CP-001-SIDECAR-BFF-HANDOFF archived done; sidecar BFF handoff packets (SIDECAR-BFF-HANDOFF and FOLLOWUP-2) complete."
   ```
3. After `done`, run the normal non-force git push:
   ```bash
   git push
   ```

Per `.orchestrator/skills/task-closeout-finalization.md`, `done` must not be called while
PR #2114 is still open. The merge guard enforces this.

## Support-Only Boundary

- This sidecar does not edit `AI_COLLABORATION_GUIDE.md`, `TARGET_ARCHITECTURE.md`, or any L1 policy file.
- This sidecar does not edit `scripts/git/auto_integrator.py` or any CI/CD workflow file.
- This sidecar does not edit runtime, registry, governance, BFF router, OpenAPI, or JSON schema files.
- This sidecar does not edit execute-plans frontend code.
- This sidecar does not approve or finalize the parent task.
- This sidecar does not attempt to merge or close PR #2114 or PR #2118.
- Claude2 records this packet-time state and asks the reviewer (Claude) to re-verify PR #2114
  state before calling `done` on the parent.

## Evidence Commands Run

```bash
# Branch and worktree check
git branch --show-current
# task/INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-SIDECAR-BFF-HANDOFF-FOLLOWUP-2

git status --short
# ?? .orchestrator/task-briefs/integration_unblock_ag_be_cp_001_sidecar_bff_handoff_ci_red_sidecar_bff_handoff_followup_2.md

# Task status checks
AI_NAME=Claude2 python3 scripts/ai_status.py show INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED
AI_NAME=Claude2 python3 scripts/ai_status.py show INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-SIDECAR-BFF-HANDOFF-FOLLOWUP-2
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-BE-CP-001

# PR list check
gh pr list --state all --search "INTEGRATION-UNBLOCK-AG-BE-CP-001" \
  --json number,title,state,headRefName,mergedAt,url --limit 10
```

Observed facts used by this packet (as of 2026-06-21):

- Parent task `INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED` is `review_approved`; Claude2 approved; owner Claude must finalize.
- PR #2114 is OPEN; parent closeout is blocked until it merges.
- PR #2116 (previous sidecar) is MERGED at `2026-06-21T17:07:09Z`.
- `AG-BE-CP-001-SIDECAR-BFF-HANDOFF` is `done`; `AG-BE-CP-001` is still `blocked` on schema/route/lifecycle.
- BFF gap matrix (G-01 through G-08) is unchanged from the original handoff packet.
- Frontend task `AG-FE-TR-002` remains `todo`, gated on `AG-BE-CP-001`.

## Reviewer Handoff

Reviewer: `Claude`

Claude review should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact and task-owned status metadata are in scope. |
| Canonical truth | No canonical docs, schemas, OpenAPI, BFF runtime, registry/governance, or frontend files changed. |
| State accuracy | Parent is `review_approved`; PR #2114 open; PR #2116 merged; `AG-BE-CP-001` blocked. |
| BFF gap forward | Eight-route gap matrix and three blockers accurately match `AG-BE-CP-001` blocker note and original handoff packet. |
| Closeout requirements | Claude's required steps for parent finalization are correctly stated. |
| No-order guard | No candidate route in the gap matrix creates a broker order, `RuntimeBinding`, or capital binding. |

Recommended reviewer approval command:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED/INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md \
  REVIEW_NOTES_ZH="Followup-2 BFF handoff packet approved: updated state (parent review_approved, PR #2114 open, PR #2116 merged), 8-route gap matrix unchanged, AG-BE-CP-001 still blocked on 3 schema/route/lifecycle items, Claude closeout steps correctly documented, support-only boundary maintained." \
  ./scripts/ai-status.sh approve INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 \
  "Followup-2 BFF/frontend handoff packet approved for parent owner absorption."
```

Recommended reviewer correction command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-CI-RED-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 \
  "Describe the factual correction, scope violation, or missing handoff detail needed before approval."
```

Prepared by Claude2 for the BFF handoff followup-2 sidecar.
