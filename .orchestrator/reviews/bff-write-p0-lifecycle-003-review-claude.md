# Review: BFF-WRITE-P0-LIFECYCLE-003

**Task:** POST /bff/runtimes/{id}/actions/StartRuntime (register in action_catalog)
**Reviewer:** Claude
**Owner:** Claude2
**Date:** 2026-05-29
**Commit reviewed:** e6bdd5c2

## Verdict: APPROVED

## Scope checked

Four files changed in commit e6bdd5c2:

1. `services/control-plane/bff/models.py` — `CommandType.START_RUNTIME = "StartRuntime"` added
2. `services/control-plane/bff/action_catalog.py` — `StartRuntime` catalog entry registered
3. `services/control-plane/bff/command_executor.py` — `_execute_start_runtime` handler + `_EXECUTORS` dispatch entry
4. `services/control-plane/bff/test_bff_write_gap_2026_05_28.py` — 23 new tests (all pass)

## Spec compliance (Card P0-3)

| Requirement | Implemented |
|---|---|
| `action_id="StartRuntime"` in catalog | ✓ `action_catalog.py:85` |
| `entity_type="Runtime"` | ✓ |
| `risk_level=HIGH` | ✓ |
| `requires_confirm_token=True` | ✓ |
| `requires_two_man=True` | ✓ |
| `cooldown_seconds=60` | ✓ |
| `required_roles=["runtime_operator", "live_owner_approver"]` | ✓ |
| `idempotency_required=True` | ✓ |
| Endpoint references `runtimes` + `StartRuntime` | ✓ `/bff/runtimes/{runtime_id}/actions/StartRuntime` |
| Executor dispatches to `/api/internal/v1/runtimes/{id}/start` | ✓ |
| `confirm_token` required (raises ValueError on missing) | ✓ |
| `runtime_id` required (raises ValueError on missing) | ✓ |
| `two_man_token` forwarded when present, absent when not | ✓ |
| Returns Pack D 202 envelope: `commandId`, `runtime_id`, `state="starting"` | ✓ |
| Default `state="starting"` when backend omits field | ✓ |
| `CommandType.START_RUNTIME` in `_EXECUTORS` dispatch table | ✓ |

## Tests verified

```
pytest services/control-plane/bff/test_bff_write_gap_2026_05_28.py -v
```
**23 passed** — all acceptance criteria covered:
- CommandType enum value and membership
- Catalog entry metadata (entity_type, risk_level, confirm, two-man, roles, idempotency, cooldown, endpoint)
- `_execute_start_runtime` success path with mock
- Correct URL dispatch (`/api/internal/v1/runtimes/rt-xyz-002/start`)
- `two_man_token` forwarded / absent cases
- `missing runtime_id` raises ValueError
- `missing confirm_token` raises ValueError
- Default state fallback to "starting"
- `execute_command` dispatch routing

```
pytest services/control-plane/bff/test_command_executor.py -v
```
**29 passed** — no regressions in existing suite.

## Pre-existing gap noted

`AlertAcknowledge` is in `CommandType` and `_EXECUTORS` but has no dedicated catalog entry (routes through `_execute_bff_action_adapter`). This gap exists prior to this commit and is unchanged by this task.

## Approval notes

Implementation is clean, narrow, and spec-aligned. The executor correctly enforces both `confirm_token` and optional `two_man_token` forwarding. No overreach beyond Card P0-3 scope. No regressions.
