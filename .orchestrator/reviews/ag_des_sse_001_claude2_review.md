# Review: AG-DES-SSE-001 — Typed workshop SSE event contract (v1.3)

Reviewer: Claude2
Date: 2026-06-21
Outcome: **APPROVED**

## Scope

Owner (Claude) delivered `services/control-plane/specs/agora/v4/workshop_stream_event.schema.json`
in commit `f6cb2c6c`, scoped to the schema file only. OpenAPI delta and bundle_index.v1_3.json
composition are explicitly deferred to the downstream task AG-XR-OPENAPI-004.

## Verification Checks

### 1. Schema byte-identity against reference
`diff` against `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/schemas/workshop_stream_event.schema.json` → **IDENTICAL**

### 2. SHA256 against bundle_index.v1_3.template.json
`sha256sum` output: `37c21e77aae56a0d5031bbc56d16e86541b5b79422b54821a0503772c307e4de`
Template expectation for `specs/agora/v4/workshop_stream_event.schema.json`: `37c21e77aae56a0d5031bbc56d16e86541b5b79422b54821a0503772c307e4de`
→ **MATCH**

### 3. Envelope fields (§C2)
All 14 envelope fields present in schema properties; required subset matches contract:
`spec_version`, `event_id`, `event_type`, `aggregate_type`, `aggregate_id`,
`sequence_no`, `event_time`, `emitted_at`, `trace_id`, `idempotency_key`, `payload` → **REQUIRED**
Optional: `causal_parent_id`, `request_id`, `data_cutoff`, `visibility`, `payload_schema` → **PRESENT**
`aggregate_type` constrained to `["strategy_workshop"]` ✅
`sequence_no` minimum: 1 ✅
`causal_parent_id` nullable (`["string", "null"]`) ✅
`additionalProperties: false` on envelope ✅

### 4. Event catalog (§C3 — 25 events)
All 25 event_type enum values verified present and ordered per contract:
workshop.* (12): snapshot, message.accepted, servant.response.started/delta/completed,
  completeness.updated, next_question.updated, patch.proposed/validated,
  version.created/selected, readiness.updated, concluded, archived
research.* (7): plan.created/approved/cancelled, run.queued/progress/completed/failed
consultation.* (2): started, completed
stream.* (2): heartbeat, error
→ **ALL 25 PRESENT, NO EXTRA ENTRIES**

### 5. Frozen file protection
`git show f6cb2c6c -- bundle_index.json bundle_index.v1_1.json bundle_index.v1_2.json` → **NO OUTPUT** (none touched)

### 6. No invented fields or routes
Schema has no fields, enums, or routes beyond the design source → **CLEAN**

### 7. Commit trailers
`LLM-Agent: Claude`, `Task-ID: AG-DES-SSE-001`, `Reviewer: Codex` (original assignment)
Note: Reviewer trailer lists Codex (original); chair reassigned review to Claude2 after dispatch.
Trailers are otherwise well-formed and pass the hook check. ✅

## Findings

No blocking issues. No invented content. Schema is a faithful, additive, correctly-hashed
landing of the v1.3 design closure source.

The commit correctly defers OpenAPI/bundle_index composition to AG-XR-OPENAPI-004 as the
compose-with note states — this is the right task boundary.

## Approval

Review approved. Task returned to owner (Claude) for final closeout per task-closeout-finalization.md.
Owner should verify the PR is still on `task/AG-DES-SSE-001` targeting `dev` and proceed
with `scripts/ai-status.sh done` after merge.
