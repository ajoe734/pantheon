# Review: BFF-B1-007 — POST /bff/v1/commands canonical command admission facade

Reviewer: Claude
Task commit: d61907ff329141fcf685f4fceafde8805c8bc439
PR: #426 (merged into dev via c1573ed7)
Review date: 2026-05-23

## Verdict: Approved

## Scope Verified

Three files changed in the task commit:
- `docs/04/pantheon_bff_api_gap_2026-05-23/BFF_API_GAP_final_integration_spec.md` — §13 added
- `services/control-plane/bff/main.py` — 8 lines in two functions
- `services/control-plane/bff/test_governance_command_submission.py` — 23 lines of additional assertions

Changes are narrow and exactly scoped to the task. Legacy `/api/v1/operator/commands` untouched.

## Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | `POST /bff/v1/commands` accepts final command schema | Pass — `_normalize_operator_command_payload` in `_submit_final_command_admission` |
| 2 | Headers honored and persisted in foundation trace/audit | Pass — `_build_foundation_command_context` wires `x_correlation_id`, `x_request_id`, `idempotency_key`; test verifies stored `trace_context` |
| 3 | Response is `CommandResponse<T>` with `trackingUrl` and `meta.idempotency` | Pass — `include_durable_meta=True` now set on canonical route; `_project_final_command_response` adds `command_id`, `commandId`, `tracking_url`, `trackingUrl`, and receipt sub-fields |
| 4 | Duplicate idempotency key replays original receipt | Pass — existing duplicate branch returns `replayed=True` meta |
| 5 | Duplicate key with different payload → 409 `IDEMPOTENCY_CONFLICT` | Pass — `_foundation_idempotency_conflict_error` path unchanged and tested |
| 6 | Live broker fail-closed | Pass — `_ensure_live_broker_scope_allowed` unchanged and tested |
| 7 | Legacy `/api/v1/operator/commands` unaffected | Pass — no changes to that route |

## Test Run

```
python3 -m pytest services/control-plane/bff/test_governance_command_submission.py \
  services/control-plane/bff/tests/test_command_replay_conflict.py \
  services/control-plane/bff/test_v5_interventions.py \
  services/control-plane/bff/test_final_precondition_errors.py -q
52 passed in 11.70s
```

## Notes

- The `include_durable_meta=True` flag was previously absent on the canonical `/bff/v1/commands` route; adding it is the minimal correct fix so `meta.idempotency` is present in the response.
- `trackingUrl` pointing to `/api/v1/operator/commands/{command_id}` preserves frontend polling compatibility as specified.
- Commit trailers are well-formed: `LLM-Agent: Codex`, `Task-ID: BFF-B1-007`, `Reviewer: Claude`, `Verified` summary included.
- PR #426 is already merged; no outstanding CI issues.
