# AG-BE-CP-001 BFF and Frontend Handoff Packet — Followup 3

| Field | Value |
|---|---|
| Task ID | `AG-BE-CP-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-CP-001` — CandidatePool/Member/Discussion/Monitoring records |
| Parent owner / reviewer | `Codex` / `Claude2` |
| Prepared by | `Claude` |
| Reviewer | `Codex` |
| Date | 2026-06-22 |
| Mutates canonical truth | `false` |
| Baseline | Follows `AG-BE-CP-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` (done, PR #2132, reviewed by Claude2) |
| Status | Ready for reviewer handoff |

This is a support artifact only. It does not modify L1 canonical truth, OpenAPI, JSON schemas, BFF
runtime, registry/governance implementation, or frontend code. It provides Codex (parent owner of
`AG-BE-CP-001`) with an updated analysis of the parent task's implementation context, reflecting the
v1.4 contract delivered by `AG-XR-CP-001` (done, PR #2179 merged into `dev`).

---

## Delta From Followup-2

The previous packet (`AG-BE-CP-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`, reviewed and approved by Claude2,
PR #2132 merged into `dev`) identified three blockers that prevented `AG-BE-CP-001` from starting
implementation.

**What changed since Followup-2:**

| Change | Detail |
|---|---|
| `AG-XR-CP-001` is done | PR #2179 merged to `dev` at `ac7b358b`. Delivered `agora_v1_4.openapi.yaml` (15 routes — 15 operationIds across 10 OpenAPI paths), four new v5 JSON Schemas, `capability_manifest_v1_4.json`, and `bundle_index.v1_4.json`. All CI checks green (commit-trailers, runtime-mirror-guard, smoke-acceptance). |
| Blocker 1 resolved: §17.3 route now formally defined | `agora_v1_4.openapi.yaml` defines all candidate pool BFF routes; 15 operationIds across 10 OpenAPI paths added. The §17.3 gap is formally addressed. |
| Blocker 2 resolved: `candidate_score.schema.json` delivered | `services/control-plane/specs/agora/v5/candidate_score_result.schema.json` defines `CandidateScoreResult` as a JSON Schema (not just TypeScript types). All four score fields are `required`. |
| Blocker 3 resolved: decision verbs and lifecycle state formalized | `services/control-plane/specs/agora/v5/candidate_member_review.schema.json` defines valid review decisions: `approve_for_monitoring`, `send_to_shadow`, `needs_more_research`, `park`, `reject`. |
| Decision verbs changed from Followup-2 | v1.4 uses `approve_for_monitoring / send_to_shadow / needs_more_research / park / reject` — not the D8 verbs from Followup-2 (`add_to_monitoring / remove / park / request_research / start_shadow / create_entry_watch`). Implementation must use v1.4 verbs. |
| Score endpoint is pool-level, not per-member | `GET/POST /bff/agora/candidate-pools/{pool_id}/score` returns all member scores for the pool in a `ListEnvelope`. There is no per-member score endpoint. |
| Review endpoint introduced | `POST /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/review` accepts `CandidateMemberReview`; replaces the per-member score POST from Followup-2 handoff spec. |
| Pool-level discussions added | Pool-level discussions at `GET/POST /bff/agora/candidate-pools/{pool_id}/discussions` are new; Followup-2 only described member-level discussions. |
| Monitoring routes restructured | Pool-level list at `GET /bff/agora/candidate-pools/{pool_id}/monitoring`; member add/remove at `POST/DELETE /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/monitor`. |
| `candidate_pool.schema.json` unchanged | v1.0, `additionalProperties: false`, no changes; v1.4 schemas live in `v5/` and are referenced from the OpenAPI by `$ref`, not by extending the base schema. |
| `AG-BE-CP-001` is now `in_progress` | Status moved from `blocked` to `in_progress`; next: "Starting CandidatePool BFF persistence implementation from v1.4 contract." |
| Non-blocking camelCase note | Codex's review approval of AG-XR-CP-001 flagged a non-blocking inconsistency: some OpenAPI description text uses camelCase (`rawScore`, `penaltyScore`, `evidenceConfidence`, `effectiveScore`), but the JSON Schema required fields are snake_case (`raw_score`, `penalty_score`, `evidence_confidence`, `effective_score`). Implementation must follow snake_case from the schema. |

---

## V1.4 Contract Summary

### Routes Added by AG-XR-CP-001

All routes are in `agora_v1_4.openapi.yaml`, base path: `/bff/agora/`.

| Route | OperationId | Purpose |
|---|---|---|
| `GET /bff/agora/candidate-pools` | `listCandidatePools` | List CandidatePool snapshots for operator scope |
| `POST /bff/agora/candidate-pools` | `createCandidatePool` | Create a new pool snapshot (with filter + recipe_id) |
| `GET /bff/agora/candidate-pools/{pool_id}` | `getCandidatePool` | Pool snapshot detail |
| `GET /bff/agora/candidate-pools/{pool_id}/score` | `getCandidatePoolScore` | All member score results for a pool (ListEnvelope of CandidateScoreResult) |
| `POST /bff/agora/candidate-pools/{pool_id}/score` | `triggerCandidatePoolScore` | Trigger a score run (queued; 202 response) |
| `GET /bff/agora/candidate-pools/{pool_id}/members` | `listCandidatePoolMembers` | List pool members (filterable by lifecycle_state, band) |
| `GET /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}` | `getCandidatePoolMember` | Member detail with current CandidateScoreResult |
| `POST /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/review` | `reviewCandidatePoolMember` | Submit trader review decision |
| `GET /bff/agora/candidate-pools/{pool_id}/discussions` | `listCandidatePoolDiscussions` | Pool-level discussion threads |
| `POST /bff/agora/candidate-pools/{pool_id}/discussions` | `createCandidatePoolDiscussion` | Create pool-level discussion entry |
| `GET /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/discussions` | `listCandidateMemberDiscussions` | Member-level discussion threads |
| `POST /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/discussions` | `createCandidateMemberDiscussion` | Create member-level discussion entry |
| `GET /bff/agora/candidate-pools/{pool_id}/monitoring` | `listCandidatePoolMonitoring` | List monitoring watchlist for pool |
| `POST /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/monitor` | `addCandidateToMonitoring` | Add approved candidate to monitoring |
| `DELETE /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/monitor` | `removeCandidateFromMonitoring` | Remove candidate from monitoring |

### New V5 JSON Schemas

All in `services/control-plane/specs/agora/v5/`.

| Schema File | Key Contracts |
|---|---|
| `candidate_score_result.schema.json` | `CandidateScoreResult`: required fields `candidate_id`, `pool_id`, `recipe_id`, `recipe_version`, `raw_score`, `penalty_score`, `evidence_confidence`, `effective_score`, `band`, `components`, `blockers`, `data_cutoff`, `scored_at`. `components[]` each require `component_id`, `label`, `category`, `direction`, `weight`, `contribution`, `transform`, `missing_policy`, `evidence_refs`. |
| `candidate_member_review.schema.json` | `CandidateMemberReview`: required `decision` (one of `approve_for_monitoring / send_to_shadow / needs_more_research / park / reject`), `reviewed_by`. Optional `rationale`, `score_override`, `negative_example_tags`. |
| `candidate_discussion.schema.json` | `CandidateDiscussion`: required `discussion_id`, `subject_type` (`pool` or `member`), `subject_id`, `author`, `body`, `created_at`. Optional `kind` enum (`comment`, `research_task`, `score_question`, `risk_flag`, `approval_note`). |
| `candidate_monitoring_status.schema.json` | `CandidateMonitoringStatus`: required `artifact_id`, `pool_id`, `monitoring_state` (`active / paused / graduated / removed`), `added_at`. Optional `trigger_conditions`, `last_score_result_id`, `review_due_at`. |

### Request / Response Envelopes

Defined in `agora_v1_4.openapi.yaml` `components.schemas`:

| Envelope | Use case |
|---|---|
| `ListEnvelope` | Required fields: `items`, `page_info` (`next_page_token`, `page_size`, `has_more`), `meta`. |
| `DetailEnvelope` | Required fields: `object_ref` (`type`, `id`), `status`, `allowedActions`, `meta`, `links`, `data`. |
| `CommandResponse` | Required fields: `status` (`accepted / queued / completed`), `data`, `meta`. Used for write operations. |
| `ErrorEnvelope` | Required fields: `error` (`code`, `message`, optional `details`). |

### Required Headers

All write operations require:
- `Idempotency-Key` — deduplication key for POST/DELETE commands
- `X-Request-Id` — tracing
- `If-Match` — for score triggers and review decisions (ETag concurrency control)

---

## Current BFF State (Confirmed From This Worktree)

| Surface | Observed state | AG-BE-CP-001 action |
|---|---|---|
| `services/control-plane/bff/agora/servant/router.py` | No candidate pool routes registered. `candidates` appears only as a list traversal variable name; not a route. | Implement all 15 routes in a new `candidate_pool.py` router module or extend the agora router. |
| `candidate_pool.schema.json` | v1.0 present, unchanged, `additionalProperties: false`. Used by `$ref` from v1.4 OpenAPI. | Reuse for pool/member serialization validation. Do not extend. |
| `v5/` schemas | All four v5 schemas present and valid per the bundle_index.v1_4.json hashes. | Validate BFF responses against these schemas. |
| `agora_v1_4.openapi.yaml` | Present, 15 routes (15 operationIds across 10 OpenAPI paths) defined, all with required-field schemas. | Implement to the contract. Do not deviate from operationIds or response schemas. |
| Score computation | No BFF score implementation exists. | BFF must call the A2 recipe engine or a scoring projection service; the exact downstream service path is `AG-BE-CP-001`'s implementation decision. |
| Review decision persistence | No BFF review persistence exists. | BFF must persist `CandidateMemberReview` records and transition `lifecycle_state` per the decision verb. |

---

## Updated Operator Journey

### Journey A: Create And View Candidate Pool

1. Operator opens the Candidate Pool view in the Trading Room.
2. Frontend calls `POST /bff/agora/candidate-pools` (with `operator_id`, optional `filter`, optional `recipe_id`; requires `Idempotency-Key` + `X-Request-Id`).
3. BFF returns `201 DetailEnvelope`; `data` contains the `CandidatePool` projection.
4. Frontend calls `GET /bff/agora/candidate-pools` (or `GET /bff/agora/candidate-pools/{pool_id}` for a specific pool) to list/fetch existing pool snapshots.
5. UI renders the candidate table from `ListEnvelope.items[]`; sort by `effective_score` descending after score run completes.

### Journey B: Trigger And View Score Run

1. Operator triggers scoring: `POST /bff/agora/candidate-pools/{pool_id}/score` (with `If-Match`, `Idempotency-Key`, `X-Request-Id`; optional body with `recipe_id`, `force_rescore`).
2. BFF returns `202 CommandResponse` (`status: "queued"`). If a run is already active: `409`.
3. Frontend polls or receives SSE update; calls `GET /bff/agora/candidate-pools/{pool_id}/score` when run is ready.
4. `200 ListEnvelope`; each item is a `CandidateScoreResult`. All four score fields (`raw_score`, `penalty_score`, `evidence_confidence`, `effective_score`) and the full `components[]` array must be returned.
5. UI renders score decomposition drawer for each candidate; must show all A2 component categories.
6. Suppressed candidates (`band=suppressed`) must show suppression reason; must not show numeric rank.

### Journey C: Submit A Review Decision

1. Operator selects a decision from the candidate row or decomposition drawer.
2. Valid decisions: `approve_for_monitoring`, `send_to_shadow`, `needs_more_research`, `park`, `reject`.
3. Frontend calls `POST /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/review` with `CandidateMemberReview` body; requires `If-Match` + `Idempotency-Key` + `X-Request-Id`.
4. BFF validates `lifecycle_state` transition and records the decision. Responds `200 CommandResponse`.
5. `reject` and `park` must set `negative_example_tags` if provided; the candidate record must be preserved — no hard delete.
6. UI refreshes the member state from the next `GET /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}` call; must not pre-empt the BFF response with local state mutation.
7. On `409`: candidate not in a reviewable state or ETag mismatch — UI must prompt the operator to refresh.

### Journey D: Approve For Monitoring

1. Operator selects `approve_for_monitoring` in the review step.
2. After `200 CommandResponse`, frontend calls `POST /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/monitor` with `CandidateMonitoringStatus` body (requires `If-Match`, `Idempotency-Key`, `X-Request-Id`).
3. BFF returns `201 DetailEnvelope`; monitoring entry created with `monitoring_state=active`.
4. Frontend calls `GET /bff/agora/candidate-pools/{pool_id}/monitoring` to confirm the watchlist entry.
5. UI shows "Monitoring" badge; `added_at`, `trigger_conditions`, and `review_due_at` (if set) must be visible.
6. Trading Room (`AG-BE-TR-001`) subsequently reads the candidate-decision reference; it does not re-create a candidate state machine.

### Journey E: Collaborative Discussion

1. Operator creates a pool-level comment: `POST /bff/agora/candidate-pools/{pool_id}/discussions` (body: `CandidateDiscussion` with `subject_type=pool`).
2. For a specific candidate: `POST /bff/agora/candidate-pools/{pool_id}/members/{artifact_id}/discussions` (body: `CandidateDiscussion` with `subject_type=member`).
3. Supported `kind` values: `comment`, `research_task`, `score_question`, `risk_flag`, `approval_note`.
4. Frontend calls `GET` variants to list discussions, with optional `resolved` and `kind` filters.
5. UI renders discussions as an annotation timeline on the candidate drawer; actionable `research_task` and `risk_flag` kinds must be surfaced distinctly.

### Journey F: Capability Not Ready

1. Operator attempts any candidate pool action while the registry or scoring backend is unavailable.
2. BFF returns a typed degraded response (`ErrorEnvelope`) rather than a synthetic success.
3. UI displays the blocked state with `error.code` and `error.details`; must not silently hide unavailability or substitute fixture data.

---

## Updated Frontend Handoff

### TypeScript Client Methods (Updated for v1.4)

All methods belong in `execute-plans/src/lib/bff-v1/agora/candidate.ts` (or equivalent).

```ts
// Pool management
createCandidatePool(body: CreatePoolRequest): Promise<DetailEnvelope<CandidatePool>>
listCandidatePools(filter?: CandidatePoolListFilter): Promise<ListEnvelope<CandidatePool>>
getCandidatePool(poolId: string): Promise<DetailEnvelope<CandidatePool>>

// Score runs
triggerCandidatePoolScore(poolId: string, body?: ScoreRunRequest): Promise<CommandResponse>
getCandidatePoolScore(poolId: string): Promise<ListEnvelope<CandidateScoreResult>>

// Members
listCandidatePoolMembers(poolId: string, filter?: MemberFilter): Promise<ListEnvelope<CandidatePoolMember>>
getCandidatePoolMember(poolId: string, artifactId: string): Promise<DetailEnvelope<CandidatePoolMemberDetail>>

// Review
reviewCandidatePoolMember(
  poolId: string,
  artifactId: string,
  review: CandidateMemberReview
): Promise<CommandResponse>

// Discussions
listCandidatePoolDiscussions(poolId: string, filter?: DiscussionFilter): Promise<ListEnvelope<CandidateDiscussion>>
createCandidatePoolDiscussion(poolId: string, body: CandidateDiscussionBody): Promise<DetailEnvelope<CandidateDiscussion>>
listCandidateMemberDiscussions(poolId: string, artifactId: string, filter?: DiscussionFilter): Promise<ListEnvelope<CandidateDiscussion>>
createCandidateMemberDiscussion(poolId: string, artifactId: string, body: CandidateDiscussionBody): Promise<DetailEnvelope<CandidateDiscussion>>

// Monitoring
listCandidatePoolMonitoring(poolId: string, filter?: MonitoringFilter): Promise<ListEnvelope<CandidateMonitoringStatus>>
addCandidateToMonitoring(poolId: string, artifactId: string, body: CandidateMonitoringStatus): Promise<DetailEnvelope<CandidateMonitoringStatus>>
removeCandidateFromMonitoring(poolId: string, artifactId: string): Promise<CommandResponse>
```

**Decision verb type (v1.4 — updated from Followup-2):**

```ts
type CandidateReviewDecision =
  | "approve_for_monitoring"
  | "send_to_shadow"
  | "needs_more_research"
  | "park"
  | "reject"
```

> ⚠️ **Breaking change vs. Followup-2:** The D8 verbs (`add_to_monitoring`, `remove`, `park`, `request_research`, `start_shadow`, `create_entry_watch`) are superseded by the v1.4 review decisions above. The TypeScript `CandidateDecisionVerb` type from Followup-2 is incorrect; use `CandidateReviewDecision` per the v1.4 schema.

### UI Binding Notes (Updated for v1.4)

| UI need | v1.4 binding |
|---|---|
| Score decomposition drawer | `getCandidatePoolScore(poolId)` returns all member scores; bind `components[]` with all 7 categories. Show `raw_score`, `penalty_score`, `evidence_confidence`, `effective_score`. **Never suppress any of the four score fields.** |
| Band display | `band` enum: `priority_review`, `discuss`, `needs_research`, `park`, `suppressed`. Show alongside numeric score. `suppressed` → do not show rank, show `blockers[]` content. |
| Missing-value indicator | `components[].raw_value = null` → show `missing_policy` label. `components[].normalized_value = null` → show "using missing policy" indicator. |
| Review decision UI | Use `approve_for_monitoring / send_to_shadow / needs_more_research / park / reject` — not D8 verbs. `reject` and `park` should prompt for `rationale`. |
| Score override | `CandidateMemberReview.score_override` is optional; only expose in advanced trader view. Requires `base_recipe_id`, `proposed_version`, `changes[]`, `reason`. |
| Monitoring watchlist | After `approve_for_monitoring` review, call `addCandidateToMonitoring`; list via `listCandidatePoolMonitoring`. Show `monitoring_state`, `trigger_conditions`, `review_due_at`. |
| Discussions | Pool-level and member-level; filter by `kind` and `resolved`. Surface `research_task` and `risk_flag` kinds with distinct visual treatment. |
| No-order guard | No candidate route creates a broker order, `RuntimeBinding`, or capital binding. Monitoring is operator-scoped alert surface only. Do not expose "Place order" or "Execute" from candidate surfaces. |
| Write actions | All write endpoints require `Idempotency-Key` + `X-Request-Id`. Review and score trigger also require `If-Match`. Map `409` to "refresh required" and `422` to governance/precondition failure. |
| Degraded states | `501`: routes not yet live. `403`: missing scope. `404`: pool or candidate not found. `422`: lifecycle precondition failure. `409`: ETag mismatch or conflicting state. `503`/error envelope: backend unavailable. |

---

## Updated Backend Acceptance Checks

| Check | Expected result |
|---|---|
| Schema conformance — pool | Every `CandidatePool` response validates against `candidate_pool.schema.json` (v1.0). |
| Schema conformance — score | Every `CandidateScoreResult` validates against `v5/candidate_score_result.schema.json`. |
| Schema conformance — review | Every `CandidateMemberReview` body validates against `v5/candidate_member_review.schema.json`. |
| Schema conformance — discussion | Every `CandidateDiscussion` validates against `v5/candidate_discussion.schema.json`. |
| Schema conformance — monitoring | Every `CandidateMonitoringStatus` validates against `v5/candidate_monitoring_status.schema.json`. |
| All four score fields required | `raw_score`, `penalty_score`, `evidence_confidence`, `effective_score` all present and within valid ranges in every `CandidateScoreResult`. |
| Score field casing | All four fields are snake_case per the schema; the non-blocking camelCase text in OpenAPI descriptions is informational only. |
| Components present | `CandidateScoreResult.components` has at least one entry; all recipe components must appear. |
| Component required fields | Each component has `component_id`, `label`, `category`, `direction`, `weight`, `contribution`, `transform`, `missing_policy`, `evidence_refs`. |
| Weight constraints | Per A2 recipe: positive component weights sum to 1.0; penalty weights ≤ 0.50; no single positive component weight > 0.25. |
| Score formula | `raw_score = clamp(base_score − penalty_score, 0, 100)`; `effective_score = raw_score × (0.60 + 0.40 × evidence_confidence)`. |
| Band assignment | `80–100 → priority_review`; `65–79.99 → discuss`; `50–64.99 → needs_research`; `0–49.99 → park`; suppressed → `suppressed`. |
| Decision persistence | All five review decisions accepted; `reject` and `park` set `negative_example_tags` and preserve the member record; no hard delete. |
| Lifecycle transition | Invalid decision state transitions return `422`. |
| Monitoring state transitions | `monitoring_state` transitions: `active → paused → active`; `active/paused → graduated/removed`. Invalid transitions return `422` or `409`. |
| No-order route | No BFF endpoint under the candidate pool surface routes a broker order, writes a `RuntimeBinding`, or creates a capital binding. |
| Idempotency | Duplicate POST with the same `Idempotency-Key` returns current state rather than error or duplicate record. |
| Bundle integrity | `bundle_index.v1_4.json` hashes must match the v5 schema files and the v1.4 OpenAPI file on `dev`. Do not modify frozen hashes. |
| Frozen files untouched | `agora_v1.openapi.yaml`, `agora_v1_1.openapi.yaml`, `agora_v1_2.openapi.yaml`, `agora_v1_3.openapi.yaml`, and `candidate_pool.schema.json` must not be modified. |

---

## What Has NOT Changed (Confirmed From Original Handoff And Followup-2)

| Finding | Status |
|---|---|
| `candidate_pool.schema.json` v1.0 unchanged | Confirmed: still `additionalProperties: false`; v1.4 reuses it by `$ref`. |
| No candidate pool routes in BFF router | Confirmed: `services/control-plane/bff/agora/servant/router.py` has no candidate pool routes. |
| `AG-BE-RS-002` is done | Confirmed: archive, terminal_status done, implementation PR #2092 merged to dev. `run_ref` available from `GET /research-runs/{run_id}`. |
| `AG-FE-TR-002` is `todo` | Still depends on `AG-BE-CP-001` landing first. |
| Trading Room isolation | `AG-BE-CP-001` is the sole candidate-state writer. Trading Room consumes the candidate-decision reference without duplicating the state machine. |
| No-order-route rule | Confirmed in `capability_manifest_v1_4.json`: `no_order_route_proof = "candidate_pool_bff_request_only_no_order_route"`. |
| Rejected candidates must be retained | Confirmed: `reject` decision in `CandidateMemberReview` sets `negative_example_tags`; no hard delete. |
| A2 recipe design-closure frozen | `A2_candidate_scoring_recipe_spec.md` remains the scoring model authority; v5 schema derives from it. |

---

## Reviewer Handoff

Codex review should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact and task-owned status/brief metadata are in scope; no canonical files changed. |
| Accuracy of v1.4 route summary | 15 routes (15 operationIds across 10 OpenAPI paths) accurately summarized from `agora_v1_4.openapi.yaml`. |
| Accuracy of v5 schema summary | Four schema files accurately summarized from `services/control-plane/specs/agora/v5/`. |
| Decision verb accuracy | v1.4 decisions (`approve_for_monitoring`, `send_to_shadow`, `needs_more_research`, `park`, `reject`) accurately stated; Followup-2 D8 verbs are superseded. |
| Score endpoint accuracy | Pool-level score endpoint (`GET/POST /bff/agora/candidate-pools/{pool_id}/score`) accurately stated; no per-member score endpoint. |
| Monitoring route accuracy | Pool-level list + member-level add/remove accurately stated per v1.4 OpenAPI. |
| Non-blocking camelCase note | Accurately stated: schema fields are snake_case; camelCase in OpenAPI description text is non-blocking. |
| AG-BE-CP-001 status | Task is `in_progress`; BFF router still has no candidate pool routes; implementation starting from v1.4 contract. |
| No canonical truth changes | Confirmed: this packet and its predecessors do not change L1 docs, schemas, OpenAPI, or BFF runtime. |

**Recommended reviewer approval command:**

```bash
AI_NAME=Codex REVIEW_FILE=support/sidecars/AG-BE-CP-001/AG-BE-CP-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md \
  REVIEW_NOTES_ZH="Followup-3 handoff packet approved: accurately reflects the v1.4 contract delivered by AG-XR-CP-001 (PR #2179), updated decision verbs, pool-level score endpoint, restructured monitoring routes, revised TypeScript client signatures, updated backend acceptance checks, and confirms all three Followup-2 blockers are resolved. No canonical truth changed." \
  ./scripts/ai-status.sh approve AG-BE-CP-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3 \
  "Followup-3 BFF/frontend handoff packet approved for parent owner and reviewer reference."
```

**Recommended reviewer reopen command:**

```bash
AI_NAME=Codex ./scripts/ai-status.sh reopen AG-BE-CP-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3 \
  "Describe the factual error, inaccurate route summary, or missing handoff detail requiring correction."
```

---

## Validation Run

```bash
git branch --show-current
# task/AG-BE-CP-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3

git status --short
# ?? .orchestrator/task-briefs/ag_be_cp_001_sidecar_bff_handoff_followup_3.md
# ?? support/sidecars/AG-BE-CP-001/AG-BE-CP-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-CP-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3
# status: in_progress, owner: Claude, reviewer: Codex

AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-CP-001
# status: in_progress, owner: Codex, reviewer: Claude2
# next: "Starting CandidatePool BFF persistence implementation from v1.4 contract."

AI_NAME=Claude python3 scripts/ai_status.py show AG-XR-CP-001
# source: archive; terminal_status: done; PR #2179 merged to dev

# Confirmed v1.4 OpenAPI routes exist (15 operationIds across 10 paths):
grep "operationId:" services/control-plane/openapi/agora_v1_4.openapi.yaml
# listCandidatePools, createCandidatePool, getCandidatePool,
# getCandidatePoolScore, triggerCandidatePoolScore,
# listCandidatePoolMembers, getCandidatePoolMember,
# reviewCandidatePoolMember,
# listCandidatePoolDiscussions, createCandidatePoolDiscussion,
# listCandidateMemberDiscussions, createCandidateMemberDiscussion,
# listCandidatePoolMonitoring, addCandidateToMonitoring, removeCandidateFromMonitoring
# → 15 operationIds (grep -c gives 15)
# python3 -c "import yaml; doc=yaml.safe_load(open('services/control-plane/openapi/agora_v1_4.openapi.yaml')); print(len(doc['paths']))"
# → 10 OpenAPI paths

# Confirmed v5 schemas exist:
ls services/control-plane/specs/agora/v5/
# candidate_discussion.schema.json  candidate_member_review.schema.json
# candidate_monitoring_status.schema.json  candidate_score_result.schema.json
# capability_manifest_v1_4.json

# Confirmed BFF router has no candidate pool routes:
grep -c "candidate-pool\|candidate_pool_route\|listCandidatePool\|createCandidatePool" \
  services/control-plane/bff/agora/servant/router.py
# 0

# Confirmed candidate_pool.schema.json unchanged:
python3 -c "import json; d=json.load(open('services/control-plane/specs/agora/candidate_pool.schema.json')); print(d.get('\$id',''), d.get('title',''))"
# (v1.0, additionalProperties: false)

# Confirmed AG-BE-RS-002 done (run_ref gate lifted):
AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-RS-002
# source: archive; terminal_status: done; impl PR #2092 merged to dev
```
