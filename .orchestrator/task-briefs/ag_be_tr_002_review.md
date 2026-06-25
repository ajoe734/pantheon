# Review: AG-BE-TR-002 — Governed TradingIntent / Handoff

**Reviewer:** Claude2  
**Review date:** 2026-06-22  
**Commit reviewed:** 147a372209fc3dfe2983ce980f4780bf23074eea  
**PR:** #2220 (merged bbc23377794638c189ce83ea0cb8254f0422eef8)

## Verdict: APPROVED

All acceptance criteria met. No blocking issues.

---

## Checklist

### Safety invariants (D1 boundary)

- ✅ No broker order routing — `no_order_route_proof` literals enforced at both Pydantic model and store boundary for all three record types (decision event / intent / handoff).
- ✅ No RuntimeBinding creation or mutation.
- ✅ No capital binding side effects.
- ✅ Withdraw records pending handoff withdrawal without any broker, RuntimeBinding, or capital side effects.

### Schema alignment

- ✅ `TradingIntent` Pydantic model field-for-field with `trading_intent.schema.json` v1. All required fields present; enum values match; `model_dump(exclude_none=True)` passes jsonschema validation (tested).
- ✅ `GovernedIntentHandoffRequest` aligns with `v4/governed_intent_handoff.schema.json`. Required fields spec_version / handoff_id / intent_id / requested_stage / handoff_type / state / strategy_id / strategy_spec_registry_id / requested_by / evidence_refs / no_order_route_proof / created_at all present.

### Stage / type / queue semantics (v1.3)

- ✅ shadow → shadow_start / shadow_research
- ✅ paper → paper_validation_request / management_governance
- ✅ canary → promotion_review_request / promotion_review (request only, no order)
- ✅ live → promotion_review_request / promotion_review (request only, no order)
- ✅ Mismatch between requested_stage and handoff_type returns 409 TRADING_INTENT_HANDOFF_NOT_ALLOWED.
- ✅ Handoff state must be draft or submitted (not accepted/rejected/converted); any other value blocked with 409.

### Idempotency

- ✅ Idempotency-Key required on all three write endpoints (decide, handoff, withdraw) — 400 on missing.
- ✅ If-Match required — 428 on missing.
- ✅ X-Request-Id required — 400 on missing.
- ✅ Duplicate Idempotency-Key returns 409 IDEMPOTENCY_CONFLICT.

### Intent state machine

- ✅ approve/modify decision creates TradingIntent with state=draft; reject/defer does not create intent.
- ✅ Successful handoff submission transitions intent state to submitted.
- ✅ Withdraw transitions intent to withdrawn and bulk-withdraws pending handoffs.
- ✅ Handoff submission blocked if intent state != draft (409).

### Tests

- ✅ 31/31 tests pass (pytest -q).
- ✅ jsonschema validation: `test_approve_decision_persists_schema_valid_trading_intent` and `test_submit_handoff_paper_persists_request_only_record` both validate against canonical schemas.
- ✅ D1 safety invariant enforced at store layer: rejects wrong or missing proof values.
- ✅ Pagination tests: no-repeat token and full-coverage no-overlap both verified.

### Scope adherence

- ✅ No changes to OpenAPI/schema files, capability allowlists, or broker routing.
- ✅ No RuntimeBinding/capital ownership changes.
- ✅ In-memory store is consistent with task scope (no durable Postgres storage per commit note).

## Notes

No blocking issues found. Minor observations (not blocking):

1. `EvidenceRef.ref_type` in the Pydantic model accepts free-string rather than the schema's enum. Acceptable because this is a BFF in-memory surface (not execution); jsonschema validation in tests passes with valid enum values.
2. `GovernedIntentHandoffRequest.requested_by` is `Dict[str, Any]` — actor enum/minLength not validated at Pydantic layer. Schema-compliance is verified by jsonschema in tests using valid fixtures.

Both are consistent with the task scope and the in-memory store posture.
