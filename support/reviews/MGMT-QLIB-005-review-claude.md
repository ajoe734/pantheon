# Review: MGMT-QLIB-005 Qlib registry admission packet

Reviewer: Claude
Date: 2026-05-15
Status: approved

## Scope

Task-owned files reviewed:
- `services/learning/qlib/activation/registry_admission.py`
- `services/learning/qlib/test_registry_admission.py`
- `support/evidence/MGMT-QLIB-005/` (README.md, registry_admission_packet.json, activation-run/)

## Findings

### Safety boundary — no blocking issues

All critical safety gates verified:

| Gate | Value | Status |
|---|---|---|
| `registry_request.registry_write_performed` | `false` | PASS |
| `registry_request.deployment_stage` | `none` | PASS |
| `registry_request.requested_artifact_state` | `candidate` | PASS |
| `registry_request.current_artifact_state` | `draft` | PASS |
| `registry_request.requested_transition` | `draft_to_candidate` | PASS |
| `downstream_scope.order_route` | `none` | PASS |
| `downstream_scope.broker_session_opened` | `false` | PASS |
| `downstream_scope.capital_binding` | `none` | PASS |
| `safety_assertions` (all 7 fields) | all `true` | PASS |

### Validation logic

- `_validate_inputs` enforces input schema and cross-reference consistency before building the packet.
- `validate_registry_admission_packet` re-enforces safety/scope invariants on the output — defense-in-depth pattern is correct.
- `_registry_entry_schema_errors` uses `Draft7Validator` against the canonical registry schema, so schema drift is caught at build time.
- `entry_criteria` checks are strict: data_sufficiency requires all floor thresholds met, not just any one.

### Test coverage

Three test cases:
1. Happy path with full artifact write+load cycle through the real activation pipeline
2. Negative: strategy_spec_id mismatch is rejected at input validation stage
3. CLI: subprocess writes packet to disk and structure is verified

All 31 tests across `test_registry_admission.py`, `test_production_activation.py`, and `test_adapter.py` pass.

### Evidence artifact

The generated `registry_admission_packet.json` is internally consistent:
- `packet_id` encodes registry_id and version.
- `checksum` is a sha256 of the serialized packet (excluding the checksum field itself).
- `source_packet_ids` back-references MGMT-QLIB-001 (dataset manifest) and MGMT-QLIB-002 (StrategySpec) correctly.
- Production dataset proof includes entitlement, freshness, PIT proof, and audit trail.

### Minor observations (non-blocking)

- The activation artifacts use `stub_lgbm` backend — the README correctly declares this. Real backend production evidence is gated on Track B infrastructure availability.
- `created_by` defaults to `"Codex"` in the CLI; this is cosmetic and not a safety concern.

## Verification commands run

```
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/learning/qlib/test_registry_admission.py services/research/qlib/test_production_activation.py services/research/qlib/test_adapter.py -q
# -> 31 passed

PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/learning/qlib/activation/registry_admission.py services/learning/qlib/test_registry_admission.py services/research/qlib/adapter/qlib_adapter.py
# -> PASS

jq '[.safety_assertions | to_entries[] | .value] | all' support/evidence/MGMT-QLIB-005/registry_admission_packet.json
# -> true
```

## Decision

Approved. The admission packet is correctly scoped to `draft -> candidate` review-only, enforces all safety boundaries, passes schema validation, and has adequate test coverage. Owner Codex should complete closeout per task-closeout-finalization.
