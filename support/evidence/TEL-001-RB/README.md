# TEL-001-RB Evidence

Task: `TEL-001-RB` — TelemetryEvent canonical schema rebaseline
Owner: `Codex`
Reviewer: `Claude`
Date: 2026-05-16

## Scope

Rebaseline `services/telemetry/telemetry_event.schema.json` against the Sprint 4 / EPIC-TELEMETRY requirement that runtime, action, and telemetry events can enter the canonical ingest path with RuntimeBinding evidence.

This task did not change ingest semantics. Existing implementation already enforced the prior TEL-001 corrections: non-empty lifecycle metrics, strict binding evidence, `frozen -> execution_mode=paper`, canonical schema tests, and `binding_id` naming.

## Added Coverage

- `services/telemetry/test_tel001_rebaseline_schema.py`
  - verifies the canonical JSON Schema is a valid draft-07 schema;
  - verifies required RuntimeBinding evidence fields remain schema-required;
  - verifies the rebaseline event surface includes runtime lifecycle, paper runtime, governance/action, kill-switch, and telemetry mismatch events;
  - sends every declared `event_type` enum value through `TelemetryIngestService` with an authoritative RuntimeBinding store.

## Verification

Commands run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest services.telemetry.test_tel001_rebaseline_schema
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest services.telemetry.test_paper_runtime_ingest_contract services.telemetry.test_paper_telemetry_packet
cd services/telemetry && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest test_capture -v
cd services/telemetry && PYTHONDONTWRITEBYTECODE=1 python3 smoke_test.py
```

Results:

- `services.telemetry.test_tel001_rebaseline_schema`: passed
- `services.telemetry.test_paper_runtime_ingest_contract` + `services.telemetry.test_paper_telemetry_packet`: passed
- `services/telemetry/test_capture.py`: 35 tests passed
- `services/telemetry/smoke_test.py`: 14/14 smoke steps passed
