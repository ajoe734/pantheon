# AG-BE-CP-001 BFF and Frontend Handoff Packet — Followup 2

| Field | Value |
|---|---|
| Task ID | `AG-BE-CP-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-CP-001` — CandidatePool/Member/Discussion/Monitoring records |
| Parent owner / reviewer | `Codex` / `Claude2` |
| Prepared by | `Claude` |
| Reviewer | `Claude2` |
| Date | 2026-06-21 |
| Mutates canonical truth | `false` |
| Baseline | Follows `AG-BE-CP-001-SIDECAR-BFF-HANDOFF` (done, PR #2109, reviewed by Codex) |
| Status | Ready for reviewer handoff |

This is a support artifact only. It does not modify L1 canonical truth, OpenAPI, JSON schemas, BFF
runtime, registry/governance implementation, or frontend code. It provides Claude2 (reviewer of
`AG-BE-CP-001`) with an updated analysis of the parent task's blocker state, incorporating the
design-closure-round2 (v1.3) context and identifying the specific reviewer decisions needed to
unblock the parent.

---

## Delta From Original Handoff Packet

The original `AG-BE-CP-001-SIDECAR-BFF-HANDOFF.md` (reviewed and approved by Codex, PR #2109
merged into `dev`) documented:

- 8 missing BFF routes (candidate pool list/detail, score GET/POST, decision, discussion, monitoring).
- A2 recipe operator journeys A–F.
- TypeScript client method signatures for `execute-plans`.
- 4 open design notes: schema extension required, §17.3 not formally defined in SD, RS-002 gate
  (since resolved), Trading Room isolation boundary.

**What has changed since that packet:**

| Change | Detail |
|---|---|
| `AG-BE-RS-002` is done | Confirmed in archive: terminal `done`, implementation PR #2092 merged into `dev`. `run_ref` is available from the live `GET /research-runs/{run_id}` route. |
| Design-closure-round2 (v1.3) landed | `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/` documents (01–08 + schemas) cover: strategy versioning/patch (A), research facade/run projection (B), workshop SSE (C), Trading Room aggregate/governed intent (D), workshop card contracts (E), winner-branch E2E and isolation (F), v1.3 OpenAPI delta. |
| Round2 unblock matrix does NOT list AG-BE-CP-001 | `07_dispatch_unblock_matrix.md` does not include `AG-BE-CP-001` in its "remains blocked until" table, indicating the SD team considers it separately addressed. |
| Gap analysis calls it "covered" | `OPEN_DESIGN_GAPS_ROUND2_FOR_SD_TEAM_2026-06-21.md` lists `AG-BE-CP-001` as: "covered (A2 recipe + candidate_pool schema) \| gated on RS-002". RS-002 is now done; the gate condition is satisfied. |
| Trading Room D8 confirmed | `design-closure-round2/04_trading_room_and_governed_intent.md` §D2: "Candidate review commands must use the canonical AG-BE-CP-001 route. The Trading Room consumes its resulting candidate-decision reference and does not create a duplicate candidate state machine." |
| Parent task blocked, waiting for Claude2 | `AG-BE-CP-001` status is `blocked`, `waiting_for: "Claude2"`. Codex (parent owner) has stopped before implementation because the acceptance criteria forbid self-created routes/fields/enums. |

---

## Current Parent Task Blocker Analysis

`AG-BE-CP-001` is blocked on three missing design artifacts. The SD team's gap analysis says the
task is "covered", but Codex correctly identified that the available artifacts do not yet satisfy
the acceptance criteria "無自創欄位/route/enum" (no self-created fields, routes, or enums).

### Blocker 1: Candidate score/review HTTP route not formally defined

| Item | Observed state |
|---|---|
| Acceptance criteria cite | "§17.3 endpoint:score" |
| `SD_2026-06-20.md` §17 | Section is only an anchor to §5 (route catalog) |
| §5.1 / §5.2 of SD | No candidate pool or score route defined |
| `agora_v1.openapi.yaml` | 61 routes; no candidate pool or score route |
| `agora_v1_3.openapi.yaml` | No candidate pool or score route |
| `design-closure-round2/08_openapi_v1_3_delta.yaml` | No candidate pool or score route |

The A2 scoring recipe (`design-closure/A2_candidate_scoring_recipe_spec.md`) defines the score
formula and TypeScript types, but does not specify an HTTP route path, method, or request/response
schema. Codex cannot implement §17.3 without a formal route definition.

**Reviewer decision required:** Claude2 must either:
- (a) Confirm that the A2 recipe spec plus the existing candidate_pool BFF pattern is sufficient
  authority for Codex to derive the route (i.e., interpret the "covered" determination as license
  to follow BFF conventions for the path and method without a separate SD route spec), OR
- (b) Raise a blocker to SD team requesting a formal §17.3 route definition (path, method,
  request/response shapes) as a prerequisite to implementation.

### Blocker 2: candidate_pool.schema.json has `additionalProperties: false` — cannot add score fields

| Item | Observed state |
|---|---|
| `candidate_pool.schema.json` | v1.0, `additionalProperties: false` at pool level AND member level |
| Score component fields | Defined in A2 spec as TypeScript types only; no JSON Schema file exists |
| `design-closure-round2/schemas/` | Contains `trading_decision_event`, `trading_room_aggregate`, `research_plan_execution`, `research_run_projection`, `strategy_readiness`, `version_patch_proposal`, `version_compare`, `workshop_card`, `workshop_stream_event`, `governed_intent_handoff` — no `candidate_score` schema |

To expose `CandidateScoreResult` (with `raw_score`, `penalty_score`, `evidence_confidence`,
`effective_score`, `band`, `components[]`) from a BFF route, the implementation needs either:
- A new `services/control-plane/specs/agora/candidate_score.schema.json`, or
- A `$schema` extension to `candidate_pool.schema.json` (requiring a version bump and new `$id`).

Both are design-team deliverables. The SD team said "covered (A2 recipe + candidate_pool schema)"
but did not deliver a JSON Schema for `CandidateScoreResult`.

**Reviewer decision required:** Claude2 must either:
- (a) Confirm that Codex may author `candidate_score.schema.json` from the A2 recipe TypeScript
  types as part of the implementation task (treating the TypeScript types as authoritative design),
  using the existing schema versioning rules to place it in `services/control-plane/specs/agora/`,
  OR
- (b) File a blocker to SD team to deliver `candidate_score.schema.json` as a prerequisite.

### Blocker 3: `lifecycle_state` transition map not formally documented

The acceptance criteria require candidate decision recording and `lifecycle_state` transitions.
Decision verbs are defined in D8 (`add_to_monitoring`, `remove`, `park`, `request_research`,
`start_shadow`, `create_entry_watch`), but the valid `lifecycle_state` values and the allowed
transitions between them are not formally documented in any SD or design-closure artifact.

| Needed | Observed |
|---|---|
| `lifecycle_state` allowed values | Not in `candidate_pool.schema.json` (which has `additionalProperties: false` and no `lifecycle_state` enum beyond what's in the member object) |
| Transition map (e.g., `candidate → review → approved/rejected`) | Not in any design-closure doc |
| Guard conditions per verb | Not defined |

**Reviewer decision required:** Claude2 must either:
- (a) Confirm the transition map can be inferred from existing docs (candidate → review →
  approved / rejected / rejected-retained) and codified as part of the implementation, OR
- (b) File a blocker to SD team requesting a formal state machine definition.

---

## SD Team "Covered" vs. Acceptance Criteria Contradiction

The `OPEN_DESIGN_GAPS_ROUND2_FOR_SD_TEAM_2026-06-21.md` says `AG-BE-CP-001` is "covered (A2 recipe
+ candidate_pool schema)" with gate condition "gated on RS-002" (now met). However, the parent
task's acceptance criteria explicitly state:

> "無自創欄位/route/enum" — No self-created fields, routes, or enums.
> "偏離設計稿即不通過" — Deviating from the design draft fails acceptance.

These two positions create a contradiction: the SD gap analysis says it is covered, but the
available design artifacts do not include an HTTP route definition or `candidate_score` JSON Schema.
Codex correctly stopped before implementation rather than self-inventing.

**This is the core decision Claude2 must make as reviewer:**
1. If Claude2 determines the A2 recipe + existing schema is sufficient design authority → unblock
   the parent by removing the blocker and providing scoped implementation guidance.
2. If Claude2 determines the acceptance criteria require formal SD artifacts → escalate to SD team
   for `candidate_score.schema.json` + §17.3 route spec + lifecycle_state transition map before
   Codex can proceed.

---

## What Has NOT Changed (Confirmed From Original Handoff)

The following findings from the original handoff remain accurate and unchanged:

| Finding | Status |
|---|---|
| 8 BFF routes remain unimplemented | Confirmed: no candidate pool or score route in any OpenAPI file or `bff/agora/servant/router.py` |
| `candidate_pool.schema.json` v1.0 is accurate | Confirmed: still `additionalProperties: false`, no score/discussion/monitoring/negative-example fields |
| A2 recipe frozen at v1.0 | Confirmed: `A2_candidate_scoring_recipe_spec.md` is the frozen spec |
| `AG-FE-TR-002` is `todo` | Confirmed; depends on `AG-BE-CP-001` landing first |
| Trading Room boundary (D8/D2) | Confirmed: Trading Room consumes AG-BE-CP-001 candidate-decision reference; does not create a second candidate state machine |
| No-order-route rule | Confirmed: no candidate decision verb creates a broker order, `RuntimeBinding`, or capital binding |
| Rejected candidates must be retained | Confirmed: `remove`/`park` verbs must retain the record as a negative example; no hard-delete |

---

## Recommended Claude2 Review Checklist

| Check | Expected |
|---|---|
| Scope | Only this support artifact and task-owned metadata are in scope; no canonical files changed. |
| Accuracy of blocker analysis | The three blockers (missing route, missing score schema, missing lifecycle map) are factually supported by the artifacts on `dev`. |
| Accuracy of "covered" vs. acceptance-criteria tension | The contradiction between the SD gap analysis and the parent task acceptance criteria is accurately stated. |
| Design-closure-round2 completeness for CP-001 | Round2 does NOT address `AG-BE-CP-001`'s specific blockers; the v1.3 OpenAPI delta has no candidate pool or score routes. |
| RS-002 gate status | `AG-BE-RS-002` is done; the `run_ref` gate is lifted. |
| Trading Room isolation | Correctly states `AG-BE-CP-001` is the sole candidate-state writer; Trading Room reads the decision reference without duplicating the state machine. |
| Reviewer decision items | Three concrete decisions identified for Claude2; each has a clear option-A (pragmatic / proceed) and option-B (escalate to SD). |
| No canonical truth changes | Confirmed: this packet and the original handoff do not change L1 docs, schemas, OpenAPI, or BFF runtime. |

**Recommended reviewer approval command:**

```bash
AI_NAME=Claude2 REVIEW_FILE=support/sidecars/AG-BE-CP-001/AG-BE-CP-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md \
  REVIEW_NOTES_ZH="Followup-2 handoff packet approved: accurately states the post-round2 blocker analysis for AG-BE-CP-001, identifies the SD covered-vs-acceptance-criteria contradiction, and provides three concrete reviewer decisions for Claude2 to unblock or escalate the parent task. No canonical truth changed." \
  ./scripts/ai-status.sh approve AG-BE-CP-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 \
  "Followup-2 BFF/frontend handoff packet approved for parent owner and reviewer reference."
```

**Recommended reviewer reopen command:**

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh reopen AG-BE-CP-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 \
  "Describe the factual error, missing context, or decision framing issue requiring correction."
```

---

## Validation Run

```bash
git branch --show-current
# task/AG-BE-CP-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2

git status --short
# ?? .orchestrator/task-briefs/ag_be_cp_001_sidecar_bff_handoff_followup_2.md

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-CP-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2
# status: in_progress, owner: Claude, reviewer: Claude2

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-CP-001
# status: blocked, owner: Codex, reviewer: Claude2, waiting_for: Claude2

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-CP-001-SIDECAR-BFF-HANDOFF
# source: archive, terminal_status: done, PR #2109 merged to dev

# Confirmed no candidate pool or score routes in v1.3 OpenAPI delta:
grep -c "candidate-pool\|candidate_pool\|candidate-score" \
  docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/08_openapi_v1_3_delta.yaml
# 0

# Confirmed round2 schemas directory has no candidate_score.schema.json:
ls docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/schemas/
# capability_manifest_v1_3.json  governed_intent_handoff.schema.json
# research_plan_execution.schema.json  research_run_projection.schema.json
# strategy_readiness.schema.json  trading_decision_event.schema.json
# trading_room_aggregate.schema.json  version_compare.schema.json
# version_patch_proposal.schema.json  workshop_card.schema.json
# workshop_stream_event.schema.json

# Confirmed OPEN_DESIGN_GAPS listing for AG-BE-CP-001:
grep "AG-BE-CP-001" \
  docs/04/pantheon_agora_cross_repo_2026-06-20/OPEN_DESIGN_GAPS_ROUND2_FOR_SD_TEAM_2026-06-21.md
# AG-BE-CP-001 | covered (A2 recipe + candidate_pool schema) | gated on RS-002
```
