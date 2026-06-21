# INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Task ID | `INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR-BFF-HANDOFF` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT` |
| Parent owner / reviewer | `Claude` / `Codex` |
| Prepared by | `Claude2` |
| Reviewer | `Claude` |
| Date | 2026-06-21 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |

This packet is a support artifact only. It does not modify L1 canonical truth,
OpenAPI, JSON schemas, BFF runtime, registry/governance implementation, or
frontend code. Its scope is to document the post-rebase-conflict BFF/frontend
handoff state after `INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT`
resolved the original integration blocker for `AG-BE-CP-001-SIDECAR-BFF-HANDOFF`.

## Resolution Summary

| Fact | Value |
|---|---|
| Original sidecar task | `AG-BE-CP-001-SIDECAR-BFF-HANDOFF` — **done** |
| Original sidecar PR | `#2109` — merged to `dev` |
| Original sidecar commit | `6c932a347db6e774aa716e7b60b2d95cf1c08919` (merge to dev) |
| Original packet location | `support/sidecars/AG-BE-CP-001/AG-BE-CP-001-SIDECAR-BFF-HANDOFF.md` |
| Integration unblock task | `INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT` — in `review` |
| Integration unblock PR | `#2115` — open, auto-merge enabled |
| Root cause documented in | `.orchestrator/task-briefs/` (root-cause task brief committed in unblock PR) |

The original AG-BE-CP-001-SIDECAR-BFF-HANDOFF packet is now in `dev` and represents
the authoritative BFF and frontend handoff materials for `AG-BE-CP-001`.
The rebase conflict was resolved through the integration-unblock workflow;
the root cause is documented and all three acceptance criteria of the parent
integration-unblock task are met.

## Canonical BFF Handoff Reference

The full BFF/frontend handoff analysis produced by the original sidecar
(`AG-BE-CP-001-SIDECAR-BFF-HANDOFF`) is the definitive reference for `AG-BE-CP-001`
BFF development. Key content in the original packet (now in `dev`):

| Section | Content |
|---|---|
| Current BFF State | 8 candidate pool BFF routes not yet implemented in `agora_v1.openapi.yaml` or `agora_v1_3.openapi.yaml`. `services/control-plane/bff/agora/servant/router.py` has no candidate pool routes. |
| BFF Query Gap Matrix | 9 gaps: pool list, pool detail, score GET, score POST (re-score), decision, discussion GET/POST, monitoring GET, candidate score schema. |
| Operator Journeys | 6 journeys (A–F): View Pool, Review Score Decomposition, Record Decision, Add to Monitoring, Request Research, Capability Not Ready (degraded state). |
| Frontend Handoff | TypeScript client module `execute-plans/src/lib/bff-v1/agora/candidate.ts` with 8 typed methods; CandidateReviewDrawer binding; A2 score decomposition display; band display; missing-value indicators; no-order guard. |
| Acceptance Checks | Schema conformance, weight rule, score formula, data quality cap, band assignment, decision persistence, lifecycle transition, no-order route, rejected candidate retention, idempotency. |
| Open Design Notes | 3 blockers remain for `AG-BE-CP-001`: (1) missing `candidate_score.schema.json` or schema extension, (2) §17.3 endpoint not formally defined in SD, (3) lifecycle transition map not defined. |

## Current AG-BE-CP-001 Parent Task State

| Field | Value |
|---|---|
| Task ID | `AG-BE-CP-001` |
| Status | `blocked` |
| Owner | `Codex` |
| Reviewer | `Claude2` |
| Waiting for | `Claude2` |
| Remaining blockers | (1) Missing §17.3 score endpoint route definition in SD; (2) Missing schema extension / `candidate_score.schema.json`; (3) Missing `lifecycle_state` transition map. |
| RS-002 gate | **Lifted** — `AG-BE-RS-002` is `done` (PR #2092 merged to `dev`, archive commit `3566d9e6`). `run_ref` field is available from `GET /research-runs/{run_id}`. |

`AG-BE-CP-001` remains `blocked` on design/SD deliverables (schema extension,
route definition, lifecycle map), **not** on the BFF handoff sidecar.
The sidecar and integration-unblock tasks are complete; their artifacts are in `dev`.

## What The Integration-Unblock Resolved

The `AG-BE-CP-001-SIDECAR-BFF-HANDOFF` PR encountered a rebase conflict during
auto-integration. The conflict arose from concurrent `dev` advancement while the
sidecar PR was in review. The `INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT`
task documented the root cause and confirmed the resolution:

- The sidecar task branch was rebased and the PR updated.
- PR #2109 subsequently merged cleanly into `dev`.
- No BFF runtime code, canonical docs, or schema files were involved in the conflict —
  the conflict was limited to support artifact and task-state files.
- The original packet content (BFF gaps, operator journeys, frontend handoff) was
  fully preserved in the merged state.

## BFF and Frontend Coordination After Resolution

Now that the original packet is in `dev`, consumers of these materials should:

| Consumer | Action |
|---|---|
| `AG-BE-CP-001` owner (Codex) | Read `support/sidecars/AG-BE-CP-001/AG-BE-CP-001-SIDECAR-BFF-HANDOFF.md` from `dev` as the canonical BFF handoff reference. Do not absorb from this sidecar's worktree; use `dev`. |
| Trading Room (`AG-BE-TR-001` / `AG-BE-TR-002`) | Await candidate pool BFF routes from `AG-BE-CP-001`. Trading Room consumes candidate-decision references; it must not create a second candidate state machine. |
| Frontend (`AG-FE-TR-002`) | Gate on `AG-BE-CP-001` route implementation. TypeScript client methods described in the original packet (`candidate.ts`) should be added only after `AG-BE-CP-001` routes land. |
| Design / SD team | Three blockers remain before `AG-BE-CP-001` can implement: (1) formal route definition for `§17.3 endpoint:score`, (2) `candidate_score.schema.json` or approved schema extension, (3) `lifecycle_state` transition map. |

## No-Order Guard (All Surfaces)

None of the candidate pool BFF routes produce a broker order, write a
`RuntimeBinding`, or create a capital binding. This constraint applies to:

- All implementation work under `AG-BE-CP-001`.
- All UI/client methods in `candidate.ts` and `CandidateReviewDrawer`.
- All Trading Room integrations that consume candidate-decision references.

The no-order invariant must be enforced at the BFF layer; downstream services
(`AG-BE-TR-001`, `AG-BE-TR-002`) must not route a broker order based solely on
a candidate pool decision verb.

## Reviewer Handoff

Claude review should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact file is in scope; no canonical docs, schemas, OpenAPI, BFF runtime, or frontend files changed. |
| Canonical truth | No L1 docs, OpenAPI, JSON schema, registry/governance runtime, or frontend code changed by this sidecar. |
| Resolution facts | `AG-BE-CP-001-SIDECAR-BFF-HANDOFF` is `done`; PR #2109 merged to `dev`; `INTEGRATION-UNBLOCK` parent in `review` with PR #2115 open; root cause documented. |
| Parent state accuracy | `AG-BE-CP-001` is `blocked` (owner `Codex`, reviewer `Claude2`); three specific blockers stated; RS-002 gate lifted (PR #2092 merged to `dev`). |
| Handoff pointer accuracy | Authoritative packet at `support/sidecars/AG-BE-CP-001/AG-BE-CP-001-SIDECAR-BFF-HANDOFF.md` in `dev`; this sidecar is a post-resolution status wrapper only. |
| No-order guard | No candidate pool surface creates a broker order, `RuntimeBinding`, or capital binding; constraint correctly stated for BFF, frontend, and Trading Room. |
| Consumer guidance | Consumers correctly directed to use the merged packet from `dev`; frontend gated on `AG-BE-CP-001` routes; Trading Room isolation boundary preserved. |

Recommended reviewer approval command:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT/INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR-BFF-HANDOFF.md \
  REVIEW_NOTES_ZH="Support-only BFF/frontend handoff packet approved: confirms original AG-BE-CP-001-SIDECAR-BFF-HANDOFF packet is in dev (PR #2109 merged), rebase conflict resolved, INTEGRATION-UNBLOCK parent in review, AG-BE-CP-001 remains blocked on design deliverables (schema/route/lifecycle-map), no-order guard and Trading Room isolation correctly stated, no canonical truth changes." \
  ./scripts/ai-status.sh approve INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR-BFF-HANDOFF \
  "Post-resolution BFF/frontend handoff packet approved."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR-BFF-HANDOFF \
  "Describe the factual correction or missing handoff detail needed before approval."
```

## Validation

```bash
git branch --show-current
# task/INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR-BFF-HANDOFF

AI_NAME=Claude2 python3 scripts/ai_status.py show INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT-SIDECAR-BFF-HANDOFF
# status: in_progress; owner: Claude2; reviewer: Claude

AI_NAME=Claude2 python3 scripts/ai_status.py show AG-BE-CP-001-SIDECAR-BFF-HANDOFF
# source: archive; terminal_status: done; PR #2109 merged to dev

AI_NAME=Claude2 python3 scripts/ai_status.py show INTEGRATION-UNBLOCK-AG-BE-CP-001-SIDECAR-BFF-HANDOFF-REBASE-CONFLICT
# status: review; next: PR #2115 open, all acceptance criteria met

AI_NAME=Claude2 python3 scripts/ai_status.py show AG-BE-CP-001
# status: blocked; owner: Codex; reviewer: Claude2; waiting_for: Claude2
```
