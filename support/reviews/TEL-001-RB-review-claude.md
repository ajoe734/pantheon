# TEL-001-RB Review — Claude

Task: `TEL-001-RB` — TelemetryEvent canonical schema (rebaseline)
Owner: Codex
Reviewer: Claude
Date: 2026-05-16
Status: **approved**

## Scope Verified

Rebaseline of `services/telemetry/telemetry_event.schema.json` against Sprint 4 / EPIC-TELEMETRY requirements. Added focused test coverage in `services/telemetry/test_tel001_rebaseline_schema.py` and evidence note at `support/evidence/TEL-001-RB/README.md`.

## Schema Assessment

- Draft-07 schema (`$schema: http://json-schema.org/draft-07/schema#`) with correct `$id` and `title`.
- All 8 required RuntimeBinding evidence fields are present in `required`: `binding_id`, `runtime_id`, `capital_pool_id`, `artifact_id`, `artifact_version`, `deployment_stage`, `plan_id`, `persona_capital_binding_id`.
- All 17 declared REBASELINE_REQUIRED_EVENT_TYPES are present in the `event_type` enum (heartbeat, runtime_health, deploy_started, deploy_completed, rollback_started, rollback_completed, pause_triggered, liquidate_triggered, paper_order_simulated, paper_fill_simulated, bracket_order_logged, order_rejection_simulated, governance_decision, approval_action, manual_override, kill_switch_action, telemetry_mirror_mismatch).
- Evidence E-5 rollback lineage co-presence enforced via `allOf` if/then constraints.
- `additionalProperties: false` at the top level and on `target`/`authority_refs` objects.
- `metrics.minProperties: 1` enforces non-empty lifecycle metric payloads.

## Ingest Service Assessment

- E-1 through E-6 evidence contract validated sequentially in `_validate_evidence_contract`.
- When `binding_store` is provided, binding_id resolves against authoritative store; all identity fields (runtime_id, capital_pool_id, artifact_id, artifact_version, plan_id, persona_capital_binding_id) cross-checked; deployment_stage matched against binding.deployment_mode; temporal window enforced.
- Replay policy correctly distinguishes write failures (safe to replay) from validation failures (must not replay without operator intervention).
- Idempotent deduplication by event_id with bounded eviction.

## Test Assessment

`test_tel001_rebaseline_schema.py`:
- `test_schema_declares_rebaseline_runtime_action_event_surface`: validates draft-07 compliance, required field list, and presence of all 17 required event types in enum.
- `test_every_declared_event_type_ingests_with_runtime_binding_evidence`: creates a live `TelemetryIngestService` with a stub authoritative binding store; sends every declared event_type through the full ingest path; asserts 0 rejections and total_ingested equals schema event count.

## Verification Results (independently reproduced)

```
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest services.telemetry.test_tel001_rebaseline_schema
→ Ran 2 tests in 1.586s — OK

PYTHONDONTWRITEBYTECODE=1 python3 -m unittest services.telemetry.test_paper_runtime_ingest_contract services.telemetry.test_paper_telemetry_packet
→ Ran 10 tests in 1.258s — OK

cd services/telemetry && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest test_capture -v
→ Ran 35 tests in 1.332s — OK

cd services/telemetry && PYTHONDONTWRITEBYTECODE=1 python3 smoke_test.py
→ 14/14 smoke steps passed — All smoke tests passed
```

## Decision

**Approved.** Scope is correctly limited to adding test coverage and evidence for the existing canonical schema — no ingest semantics changed. All binding evidence fields required, all declared event types reachable through the ingest path with authoritative binding cross-validation. All 4 verification suites pass independently.

Returning to Codex for finalization.
