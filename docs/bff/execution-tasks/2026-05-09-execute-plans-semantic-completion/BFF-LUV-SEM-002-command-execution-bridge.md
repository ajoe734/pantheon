# BFF-LUV-SEM-002 — Command Execution Bridge

Date: 2026-05-09
Owner lane: control-plane command facade
Reviewer lane: integration / acceptance

## Problem

Several final BFF v1 command paths are currently route-compatible but use generic accepted/completed receipts. They need to submit domain commands through the existing command store / executor path and expose truthful command receipts.

Affected families:

- `POST /bff/actions/{entityType}/{entityId}/{actionId}`
- deployment create and patch
- rebalance patch
- audit export
- confirm-token create, read, delete, redeem
- v5 intervention commands
- v5 sentinel finding status and remediation commands

## Scope

- Replace generic `_final_command_payload` usage with domain-aware command submission where a command type exists.
- Keep idempotency conflict behavior and final error envelope semantics.
- Add status/readback proof for created commands.
- Preserve existing route registration and OpenAPI contract.

## Non-Scope

- Do not enable real-capital or live broker side effects.
- Do not bypass governance / two-man / command preconditions.

## Acceptance

- Each affected command route writes a command record or an explicit domain receipt backed by a durable store.
- Duplicate idempotency key replays return the same command receipt.
- Conflicting idempotency payloads return final 409 envelope.
- Focused command tests plus `test_execute_plans_final_live_wiring_contract.py` pass.

## Implementation Notes

- Added domain command types and object targets for execute-plans final command surfaces:
  deployment create/patch, rebalance patch, audit export, confirm-token create/delete/redeem, v5 intervention action, sentinel finding status, and sentinel remediation build/execute.
- Replaced the affected final-route generic receipt path with a shared command-store-backed receipt helper.
- `POST /bff/actions/{entityType}/{entityId}/{actionId}` now replays/conflicts against the durable command store through the shared action helper instead of relying only on a generic in-memory receipt.
- Confirm-token reads now project the latest command-store record for the token and expose created/redeemed/deleted status without creating live side effects.
- Command receipts include `/api/v1/operator/commands/{command_id}` tracking URLs and preserve the existing final `CommandResponse` envelope.
- New command catalog entries were added for all new `CommandType` values so `/bff/actions` remains complete.

## Verification

Final closeout verification (commit b02bce71, 2026-05-09):

- `python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/models.py services/control-plane/bff/test_final_command_execution_bridge.py` — OK
- `python3 -m pytest services/control-plane/bff/test_final_command_execution_bridge.py -q` — 11 passed (bridge suite with server-generated-id replay and durable conflict regression tests)
- `python3 -m pytest services/control-plane/bff/test_final_command_execution_bridge.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/test_bff_governance_runtime_risk_audit_contract.py services/control-plane/bff/test_bff_capital_ranking_rebalance_contract.py services/control-plane/bff/test_v5_interventions.py services/control-plane/bff/test_final_precondition_errors.py services/control-plane/bff/test_governance_command_submission.py services/control-plane/bff/test_execute_plans_contract_registry.py services/control-plane/bff/test_command_executor.py services/control-plane/bff/test_final_contract_primitives.py services/control-plane/bff/test_action_catalog.py -q` — 138 passed, 13 warnings

Observed warnings are pre-existing route/OpenAPI duplicate operation-id and `datetime.utcnow()` deprecation warnings in BFF tests. No new warnings introduced by this task.
