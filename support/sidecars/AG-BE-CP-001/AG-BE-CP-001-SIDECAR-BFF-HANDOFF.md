# AG-BE-CP-001 BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Task ID | `AG-BE-CP-001-SIDECAR-BFF-HANDOFF` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-CP-001` — CandidatePool/Member/Discussion/Monitoring records |
| Parent owner / reviewer | `Codex` / `Claude2` |
| Prepared by | `Claude` |
| Reviewer | `Codex` |
| Date | 2026-06-21 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |

This packet is a support artifact only. It does not modify L1 canonical truth,
OpenAPI, JSON schemas, BFF runtime, registry/governance implementation, or
execute-plans frontend code. It summarizes the BFF query gaps, operator journey,
and frontend handoff boundaries for `AG-BE-CP-001`; the parent owner decides
whether and how to absorb it into the main implementation.

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates ownership and lifecycle; support packets do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/ag_be_cp_001_sidecar_bff_handoff.md` | Sidecar is support-only: BFF query gap, operator journey, frontend handoff materials; no canonical truth changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support changes require explicit scope and narrow commit discipline. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo changes must pass task commit, PR, merge, and owner closeout before `done`. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-CP-001-SIDECAR-BFF-HANDOFF` | Sidecar is `in_progress`, owner `Claude`, reviewer `Codex`, helper parent `AG-BE-CP-001`, helper kind `bff_handoff_packet`. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-CP-001` | Parent is `blocked`; owner `Codex`, reviewer `Claude2`; depends on `AG-BE-RS-002`; blocker: no candidate pool/score HTTP route defined in any OpenAPI file; `candidate_pool.schema.json` has `additionalProperties: false` and no score, discussion, monitoring, or negative-example fields. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-FE-TR-002` | Status `todo`; depends on `AG-FE-TR-001`, `AG-BE-CP-001`, and `AG-XR-OPENAPI-004`; needs CandidateReviewDrawer and entry/exit queues binding A2 score components. |
| `services/control-plane/specs/agora/candidate_pool.schema.json` | `CandidatePool` v1.0 schema: pool-level fields (`pool_id`, `operator_id`, `filter`, `candidates`, `total`, `snapshot_at`); each candidate member has `artifact_id`, `strategy_ref`, `title`, `lifecycle_state`, `producing_persona_id`, `sharpe_summary`, `run_ref`, `created_at`; `additionalProperties: false` at both levels. No score components, discussion, monitoring, or negative-example fields. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/A2_candidate_scoring_recipe_spec.md` | A2 recipe frozen v1.0: scoring formula (`raw_score`, `penalty_score`, `effective_score`, `confidence_multiplier`); `CandidateScoreComponent` and `CandidateScoreResult` TypeScript contracts; 7 normalization transforms; missing-value policy; default winner-branch recipe (8 positive + 4 penalty components); weight constraints; score bands; UI decomposition requirements. Unblocks `AG-BE-CP-001`. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/04_trading_room_and_governed_intent.md` | D8: "Candidate review commands must use the canonical AG-BE-CP-001 route. The Trading Room consumes its resulting candidate-decision reference and does not create a duplicate candidate state machine." Candidate decisions: `add_to_monitoring`, `remove`, `park`, `request_research`, `start_shadow`, `create_entry_watch`. Rejected candidates retained as negative evidence. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/OPEN_DESIGN_GAPS_ROUND2_FOR_SD_TEAM_2026-06-21.md` | `AG-BE-CP-001` listed as "covered (A2 recipe + candidate_pool schema)" and "gated on RS-002". The candidate pool schema and A2 recipe are present; however, the route contract and extended schema fields (score, discussion, monitoring, negative-example) are missing. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/SD_2026-06-20.md` §17 | §17 is only an anchor to §5 (route catalog); `candidate_pool.schema.json` is under `agora.research.v1`. No candidate pool BFF routes appear in §5.1 or §5.2. The "§17.3 endpoint:score" referenced in the parent acceptance criteria is not defined in the SD. |
| `services/control-plane/openapi/agora_v1.openapi.yaml` | No candidate pool or score BFF route. The v1 OpenAPI covers 61 routes; candidate pool is absent. |
| `services/control-plane/openapi/agora_v1_3.openapi.yaml` | No candidate pool or score BFF route. |
| `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` | BFF is the sole frontend aggregation point; candidate pool routes must return typed degraded/blocked states when downstream registry is unavailable. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## Current BFF State Observed In This Worktree

| Surface | Observed state | Handoff meaning |
|---|---|---|
| `GET /bff/agora/candidate-pools` | Not implemented. | AG-BE-CP-001 must add a list route returning `CandidatePool` envelope for the operator's scope. |
| `GET /bff/agora/candidate-pools/{pool_id}` | Not implemented. | AG-BE-CP-001 must return a schema-conformant `CandidatePool` snapshot with candidates for a specific pool. |
| `GET /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/score` | Not implemented. | AG-BE-CP-001 must return a `CandidateScoreResult` computed by the A2 recipe for a specific candidate artifact. |
| `POST /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/score` | Not implemented. | AG-BE-CP-001 must trigger A2 recipe re-scoring for a candidate and return the updated `CandidateScoreResult`. |
| `POST /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/decision` | Not implemented. | AG-BE-CP-001 must record a candidate decision (`add_to_monitoring`, `remove`, `park`, `request_research`, `start_shadow`, `create_entry_watch`) and transition `lifecycle_state`. |
| `GET /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/discussions` | Not implemented. | AG-BE-CP-001 must list discussion records attached to a candidate member. |
| `POST /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/discussions` | Not implemented. | AG-BE-CP-001 must persist a discussion annotation on a candidate member. |
| `GET /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/monitoring` | Not implemented. | AG-BE-CP-001 must return monitoring record(s) for a candidate in `add_to_monitoring` state. |
| `candidate_pool.schema.json` | Present and valid; `CandidatePool` v1.0 + member fields. `additionalProperties: false` at both levels. | Score, discussion, monitoring, and negative-example fields are absent. Parent owner must request schema extension from SD/design before adding these fields — cannot self-invent. |
| A2 `CandidateScoreResult` schema | Defined in `A2_candidate_scoring_recipe_spec.md` (TypeScript types only). No JSON Schema file in `services/control-plane/specs/agora/`. | Parent owner needs a formal `candidate_score.schema.json` (or an extension to `candidate_pool.schema.json`) to validate score responses. This is a design-team deliverable. |
| `services/control-plane/bff/agora/servant/router.py` | No candidate pool routes; only internal helpers reference "candidate" as a list-traversal variable name. | No implementation work has started for the candidate pool BFF surface. |

## Parent Scope Boundary

`AG-BE-CP-001` owns:

- Candidate pool listing and snapshot endpoints under `agora.research.v1`.
- A2 recipe score computation per candidate artifact (per `A2_candidate_scoring_recipe_spec.md`).
- Candidate decision recording and `lifecycle_state` transitions: `candidate → review → approved / rejected`.
- Rejected candidate retention as negative/preference examples (not hard-deleted).
- Discussion and monitoring record persistence per candidate member.
- §17.3 `endpoint:score` — the canonical candidate score/review HTTP path.

`AG-BE-CP-001` does **not** own:

- `RuntimeBinding`, capital binding, broker order, or live/paper governance promotion.
- Trading Room decision events (`AG-BE-TR-001` owns the decision-event queue; it consumes AG-BE-CP-001's candidate-decision reference).
- Research run dispatch or projection (`AG-BE-RS-001` / `AG-BE-RS-002` own this).
- `TradingIntent` creation (`AG-BE-TR-002` owns this; it is triggered from Trading Room decisions only).

Dependencies: `AG-BE-CP-001` depends on `AG-BE-RS-002` merging first because the candidate pool is expected to include `run_ref` linking candidates to their research run projections.

## BFF Query Gap Matrix

| Gap | Needed BFF surface | Parent disposition |
|---|---|---|
| Candidate pool list is missing | `GET /bff/agora/candidate-pools` returning a filtered `CandidatePool` envelope for the operator's scope. Supports filters: `asset_classes`, `strategy_families`, `lifecycle_states`, `persona_ids`. | `AG-BE-CP-001` primary. Gated on schema extension for score/discussion/monitoring fields. |
| Candidate pool detail is missing | `GET /bff/agora/candidate-pools/{pool_id}` returning a specific pool snapshot with member list. | `AG-BE-CP-001` primary. |
| Candidate score detail is missing | `GET /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/score` returning a `CandidateScoreResult` with all A2 components. Must show `raw_score`, `penalty_score`, `evidence_confidence`, `effective_score`, `rank`, `band`, and all `components` (including `normalizedValue`, `contribution`, `missingPolicy`, `evidenceRefs`). | `AG-BE-CP-001` primary. Blocked on formal `candidate_score.schema.json` JSON Schema from design team. |
| Candidate re-score trigger is missing | `POST /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/score` to trigger A2 recipe re-computation with an optional recipe version override. | `AG-BE-CP-001` primary. Blocked on formal schema and route definition. |
| Candidate decision recording is missing | `POST /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/decision` accepting one of: `add_to_monitoring`, `remove`, `park`, `request_research`, `start_shadow`, `create_entry_watch`. `remove`/`park` must retain the candidate as a negative example; must not hard-delete. | `AG-BE-CP-001` primary. Blocked on `lifecycle_state` transition map in design. |
| Candidate discussion is missing | `GET` / `POST /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/discussions` for listing and creating discussion annotations. | `AG-BE-CP-001` primary. Blocked on schema extension. |
| Candidate monitoring record is missing | `GET /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/monitoring` for listing monitoring records once a candidate enters `add_to_monitoring`. | `AG-BE-CP-001` primary. Blocked on schema extension. |
| Candidate score schema is missing | `candidate_score.schema.json` (or an approved extension to `candidate_pool.schema.json`) mapping A2 `CandidateScoreResult` to a JSON Schema. | Design-team deliverable; `AG-BE-CP-001` cannot self-create fields due to `additionalProperties: false` constraint. |
| Frontend candidate review client is missing | TypeScript client method(s) in `execute-plans/src/lib/bff-v1/agora/` for pool list, pool detail, score detail, re-score, and decision recording. | `AG-FE-TR-002`, after `AG-BE-CP-001` routes land. |
| CandidateReviewDrawer binding is missing | Drawer must show A2 score decomposition: `raw_score`, `confidence`, `risk_penalty`, `effective_score`, all `components` (category, normalized value, weight, contribution, evidence, missing/cap reason, recipe version). Must not reduce to a single composite number. | `AG-FE-TR-002`; gate on `AG-BE-CP-001`. |

## Operator Journey

### Journey A: View The Candidate Pool

1. Operator opens the Candidate Pool view in the Trading Room.
2. Frontend calls `GET /bff/agora/candidate-pools` with optional filters (`lifecycle_states`, `strategy_families`, `persona_ids`) through the BFF client.
3. BFF returns a `CandidatePool` envelope containing candidate members sorted by `effective_score` descending.
4. UI renders the candidate table showing: `Rank`, `Symbol/artifact_id`, `Effective Score`, `Confidence`, top 3 positive drivers, top 2 penalties, `Data quality badge`, and `Status` (from A2 §10).
5. UI must always display the `band` label (`priority_review`, `discuss`, `needs_research`, `park`, `suppressed`) alongside the numeric score.
6. Suppressed candidates (`band=suppressed`) must display the suppression reason; they must not show a numeric rank.

### Journey B: Review A Candidate Score Decomposition

1. Operator clicks the score for a candidate to open the score decomposition drawer.
2. Frontend calls `GET /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/score`.
3. BFF returns a `CandidateScoreResult` with all A2 components including per-component `rawValue`, `normalizedValue`, `weight`, `contribution`, `transform`, `missingPolicy`, `evidenceRefs`, and `explanation`.
4. UI renders the decomposition drawer showing: `raw_score`, `penalty_score`, `evidence_confidence`, `effective_score`, and each component. Missing-value policy or cap reason must be visible.
5. UI must not substitute a single star-rating or summary score for the full decomposition (A2 §10).
6. Recipe `recipeId` and `recipeVersion` must be shown so operators can identify which recipe version produced the score.

### Journey C: Record A Candidate Decision

1. Operator selects a decision action from the candidate row or decomposition drawer.
2. Allowed decisions (from D8): `add_to_monitoring`, `remove`, `park`, `request_research`, `start_shadow`, `create_entry_watch`.
3. Frontend calls `POST /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/decision` with the chosen verb.
4. BFF validates the `lifecycle_state` transition (e.g., `candidate → review → approved/rejected`) and records the decision.
5. `remove` and `park` must retain the candidate as a negative/preference example; BFF must not hard-delete the record.
6. BFF returns the updated candidate member with the new `lifecycle_state`.
7. UI refreshes the candidate row status immediately; it must not pre-empt the BFF response with a local state mutation.

### Journey D: Add A Candidate To Monitoring

1. Operator selects `add_to_monitoring` for a candidate that passes score gate.
2. BFF transitions `lifecycle_state` to `review` or creates a monitoring record.
3. Frontend calls `GET /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/monitoring` to confirm the monitoring record exists.
4. UI shows a "Monitoring" badge on the candidate; monitoring start timestamp and trigger conditions must be visible.
5. Trading Room (`AG-BE-TR-001`) subsequently reads the candidate-decision reference to include the candidate in the decision-event queue — it does not re-create a candidate state machine.

### Journey E: Request Additional Research

1. Operator selects `request_research` for a candidate needing more evidence.
2. BFF records the decision and links the request to the candidate member.
3. The research request must eventually create a `ResearchPlan` via `AG-BE-RS-001` / `AG-BE-RS-002` routes — it must not route a broker order or create a `RuntimeBinding`.
4. UI shows the research-pending state on the candidate until the research result updates the `run_ref`.

### Journey F: Capability Not Ready

1. Operator attempts any candidate pool action while the registry or scoring backend is unavailable.
2. BFF returns a typed degraded response rather than a synthetic success.
3. UI displays the blocked state with error type and `blocking_reasons` per the BFF HA policy; it must not silently hide the unavailability or substitute fixture data.

## Frontend Handoff

| UI / client need | Binding guidance |
|---|---|
| BFF client | Add typed methods to a new or extended `execute-plans/src/lib/bff-v1/agora/candidate.ts` module (or extend the research module if the router groups them together). Pages must not call the registry or scoring backend directly. |
| Fallback posture | Live strict behavior. Do not add local fixture fallback, synthetic candidate data, or direct service fanout. |
| Pool list | `listCandidatePools(filter?: CandidatePoolFilter)` → bind `candidates[]` with `lifecycle_state`, `effective_score`, `band`, and `producing_persona_id`. Sort by `effective_score` descending by default. |
| Pool detail | `getCandidatePool(poolId: string)` → same shape as pool list item but full member list. Show `snapshot_at` so operator knows data freshness. |
| Score detail | `getCandidateScore(poolId, artifactId)` → bind `CandidateScoreResult.components[]` in decomposition drawer. Require `rawValue`, `normalizedValue`, `weight`, `contribution`, `transform`, `missingPolicy` to be present or explicitly null. |
| Re-score | `rescoreCandidate(poolId, artifactId, options?)` → show "Recalculating…" state during the POST; update decomposition drawer on response. |
| Decision | `recordCandidateDecision(poolId, artifactId, verb: CandidateDecisionVerb, reason?: string)` → map `409` to refresh-required, `422` to governance/precondition failure. |
| Discussions | `listCandidateDiscussions(poolId, artifactId)` / `addCandidateDiscussion(poolId, artifactId, body)` → render as annotation timeline on the candidate drawer. |
| Monitoring record | `getCandidateMonitoring(poolId, artifactId)` → show monitoring badge and start timestamp when record exists. |
| Score decomposition | CandidateReviewDrawer must show all 7 A2 component categories (`alpha`, `confidence`, `liquidity`, `risk`, `execution`, `data_quality`, `custom`) with full breakdown; must not collapse to a single composite number per A2 §10. |
| Band display | Always show `band` alongside numeric score: `priority_review` → high-emphasis badge; `discuss` → normal; `needs_research` → yellow badge; `park` → muted; `suppressed` → do not show rank, show suppression reason only. |
| Missing-value indicators | When any component's `rawValue` is null, display the `missingPolicy` label. When `data_quality < 0.50`, cap indicator and show "needs more research" tooltip. |
| No-order guard | No candidate decision verb creates a broker order, `RuntimeBinding`, or capital binding. UI must not expose "Place order" or "Execute" controls from any candidate route response. |
| Write actions | Decision and discussion POST must use idempotency keys; map `409` to refresh-required, `422` to governance/precondition failure. |
| Degraded state | `501`: feature not implemented (show coming-soon if gated on RS-002). `403`: missing scope. `404`: pool or candidate not found (clear stale view). `422`: governance or lifecycle precondition failure. `503`/blocked: registry unavailable with `blocking_reasons`. |

Suggested frontend client methods (all to be placed in `candidate.ts` or equivalent):

```ts
listCandidatePools(filter?: CandidatePoolFilter): Promise<CandidatePoolList>
getCandidatePool(poolId: string): Promise<CandidatePool>
getCandidateScore(poolId: string, artifactId: string): Promise<CandidateScoreResult>
rescoreCandidate(poolId: string, artifactId: string, options?: RescoreOptions): Promise<CandidateScoreResult>
recordCandidateDecision(poolId: string, artifactId: string, verb: CandidateDecisionVerb, reason?: string): Promise<CandidateMember>
listCandidateDiscussions(poolId: string, artifactId: string): Promise<CandidateDiscussionList>
addCandidateDiscussion(poolId: string, artifactId: string, body: CandidateDiscussionBody): Promise<CandidateDiscussion>
getCandidateMonitoring(poolId: string, artifactId: string): Promise<CandidateMonitoringRecord>
```

`CandidateDecisionVerb` type: `"add_to_monitoring" | "remove" | "park" | "request_research" | "start_shadow" | "create_entry_watch"`

## Suggested Backend Acceptance Checks

| Check | Expected result |
|---|---|
| Schema conformance | Every `CandidatePool` response validates against `services/control-plane/specs/agora/candidate_pool.schema.json`. Every `CandidateScoreResult` validates against the (to-be-created) `candidate_score.schema.json`. |
| Required pool fields | `spec_version`, `pool_id`, `operator_id`, `candidates`, `snapshot_at` all present. |
| Required score fields | `candidateId`, `recipeId`, `recipeVersion`, `rawScore`, `penaltyScore`, `evidenceConfidence`, `effectiveScore`, `band`, `components`, `dataCutoff` all present. |
| Weight rule | Positive component weights sum to 1.0; penalty weights do not exceed 0.50; no single positive component weight > 0.25. |
| Score formula | `raw_score = clamp(base_score - penalty_score, 0, 100)`; `effective_score = raw_score × (0.60 + 0.40 × evidence_confidence)`. |
| Data quality cap | When any component `data_quality` normalized value < 0.50, `effective_score` is capped at 49 and `band` is `needs_research` or `park`. |
| Missing critical liquidity | When `liquidity` component is missing and strategy enters positions, candidate must not advance to `approved_for_monitoring`. |
| Band assignment | Bands match A2 §7 thresholds: 80–100 → `priority_review`, 65–79.99 → `discuss`, 50–64.99 → `needs_research`, 0–49.99 → `park`. |
| Decision persistence | All 6 decision verbs are accepted; `remove`/`park` retain the candidate record with a negative-example flag rather than deleting it. |
| Lifecycle transition | `lifecycle_state` advances through the allowed transition map; invalid transitions return `422`. |
| No-order route | No BFF endpoint under the candidate pool surface routes a broker order, writes a `RuntimeBinding`, or creates a capital binding. |
| Rejected candidate retention | `remove` decision sets `lifecycle_state=rejected` but preserves the member record for Shadow / preference learning. |
| Idempotency | Duplicate decision POST with the same verb and idempotency key returns the current candidate state rather than an error or duplicate record. |

## Open Design Notes

### 1. Schema extension is required before implementation

`services/control-plane/specs/agora/candidate_pool.schema.json` has `additionalProperties: false` at both the pool and member level. To add score-component fields, discussion refs, monitoring refs, or negative-example flags, the design team must either:

- Extend `candidate_pool.schema.json` (requires versioning to a new `$id`), or
- Create a sibling `candidate_score.schema.json` for the `CandidateScoreResult` shape.

Parent owner (`Codex`) must not self-create these fields. A blocker toward `Claude2` (reviewer) or SD is appropriate if the schema extension has not landed before implementation begins.

### 2. §17.3 score endpoint is not formally defined in the SD

The parent acceptance criteria cite "§17.3 endpoint:score", but `SD_2026-06-20.md §17` is only an anchor to §5 (route catalog). §5.1 and §5.2 contain no candidate pool or score routes. The A2 recipe spec defines the scoring model but does not specify an HTTP route path. Parent owner should request an explicit route definition (path, method, request/response shapes) from SD or design-closure-round2 before coding the endpoint.

### 3. Gated on AG-BE-RS-002

`AG-BE-CP-001` depends on `AG-BE-RS-002` for the `run_ref` field that links candidate members to their research run projections. Until `AG-BE-RS-002` closes, candidate members may reference run IDs but the projection detail will be unavailable. BFF should return candidate pool data with `run_ref` as an opaque string; the frontend must not attempt to follow it to the RS-002 projection until that route is live.

### 4. Trading Room isolation

`AG-BE-TR-001` and `AG-BE-TR-002` consume the candidate-decision reference produced by `AG-BE-CP-001` routes. They do not create a second candidate state machine. The Trading Room `decision-events` projection is read-only from the candidate state perspective. AG-BE-CP-001 must be the sole writer of `lifecycle_state` transitions for candidates; the Trading Room may only reference the result.

## Reviewer Handoff

Codex review should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact and task-owned status/brief metadata are in scope. |
| Canonical truth | No canonical docs, schemas, OpenAPI, BFF runtime, registry/governance, or frontend files changed by this sidecar. |
| Factual alignment | `AG-BE-CP-001` is `blocked` (owner `Codex`, reviewer `Claude2`); `AG-BE-RS-002` is `todo`; `AG-FE-TR-002` is `todo`; no candidate pool BFF route exists in any OpenAPI file. |
| Schema accuracy | `candidate_pool.schema.json` accurately described: v1.0, `additionalProperties: false`, no score/discussion/monitoring/negative-example fields. |
| A2 recipe accuracy | A2 scoring formula, component contract, weight rules, bands, and default winner-branch recipe accurately reflected from `design-closure/A2_candidate_scoring_recipe_spec.md`. |
| Open design note accuracy | Schema extension is genuinely required before parent owner can implement; §17.3 is not formally defined in the SD; gating on RS-002 is correctly stated. |
| Trading Room boundary | Correct that `AG-BE-CP-001` is the sole candidate-state writer; Trading Room consumes the decision reference without duplicating the state machine. |
| No-order guard | All journeys and acceptance checks correctly exclude broker orders, `RuntimeBinding`, and capital binding. |

Recommended reviewer approval command:

```bash
AI_NAME=Codex REVIEW_FILE=support/sidecars/AG-BE-CP-001/AG-BE-CP-001-SIDECAR-BFF-HANDOFF.md \
  REVIEW_NOTES_ZH="Support-only BFF/frontend handoff packet approved: it records the CandidatePool/score/decision/discussion/monitoring BFF gap surfaces, A2 recipe operator journeys, frontend client/drawer boundaries, no-order-route guardrails, schema extension requirements, and AG-BE-CP-001 versus AG-BE-TR-001/TR-002 ownership boundary without modifying canonical truth or runtime files." \
  ./scripts/ai-status.sh approve AG-BE-CP-001-SIDECAR-BFF-HANDOFF \
  "Support-only AG-BE-CP-001 BFF/frontend handoff packet approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Codex ./scripts/ai-status.sh reopen AG-BE-CP-001-SIDECAR-BFF-HANDOFF \
  "Describe the factual correction, ownership-boundary issue, or missing handoff detail needed before approval."
```

## Validation Run

Commands run from this sidecar worktree:

```bash
git branch --show-current
# task/AG-BE-CP-001-SIDECAR-BFF-HANDOFF

git status --short
# ?? .orchestrator/task-briefs/ag_be_cp_001_sidecar_bff_handoff.md

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-CP-001-SIDECAR-BFF-HANDOFF
AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-CP-001
AI_NAME=Claude python3 scripts/ai_status.py show AG-FE-TR-002
python3 -m json.tool services/control-plane/specs/agora/candidate_pool.schema.json
grep -n "candidate\|score\|pool" services/control-plane/openapi/agora_v1.openapi.yaml
grep -n "candidate\|score\|pool" services/control-plane/openapi/agora_v1_3.openapi.yaml
grep -n "§17\|17\.3\|candidate.pool" docs/04/pantheon_agora_cross_repo_2026-06-20/SD_2026-06-20.md
```
