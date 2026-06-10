# Review: BFF-WRITE-P1-AGORA-009 — POST /bff/runtimes

**Reviewer**: Claude2
**Task**: BFF-WRITE-P1-AGORA-009
**Owner**: Codex2
**PR**: #618 (merge commit 314e3379)
**Verdict**: APPROVED

## Scope

Card P1-9: Add `POST /bff/runtimes` entity-create endpoint (runtime binding in `stopped` state).

## Checklist

| Requirement | Status | Notes |
|---|---|---|
| Endpoint exists at `POST /bff/runtimes` | ✅ | `main.py:37934` |
| Status code 201 | ✅ | |
| Required fields validated | ✅ | name, persona_id, binding_id, deployment_plan_id |
| runtime_kind validated (paper\|live only) | ✅ | 422 VALIDATION_FAILED for invalid |
| Binding conflict returns 409 RESOURCE_CONFLICT | ✅ | `_raise_if_runtime_binding_conflict` |
| Response shape matches spec | ✅ | id, name, state="stopped", persona_id, binding_id, deployment_plan_id, runtime_kind, created_at |
| meta.evidenceKind = "runtime.create" | ✅ | |
| SSE events published | ✅ | `runtime.created` + `management.runtime-status` to `_sse_buffers["runtime"]` |
| Idempotency (replay returns original) | ✅ | `_GOV_BFF_IDEMPOTENCY` |
| Idempotency conflict (same key, different payload) | ✅ | 409 IDEMPOTENCY_CONFLICT |
| Operator role check | ✅ | `_require_operator_role` |

## Test Results

```
pytest test_bff_write_gap_2026_05_28.py -q
6 passed in 3.16s
```

All 3 runtime-specific tests pass:
- `test_post_bff_runtimes_creates_stopped_runtime_and_replays_idempotently` ✅
- `test_post_bff_runtimes_rejects_binding_that_already_has_runtime` ✅
- `test_post_bff_runtimes_validates_runtime_kind` ✅

## Notes

Implementation is clean and follows established BFF patterns. Helpers (`_runtime_create_required_string`, `_raise_if_runtime_binding_conflict`, `_project_runtime_create_response`) are correctly factored and reuse the `_GOV_BFF_IDEMPOTENCY` ledger shared with other governance write endpoints.
