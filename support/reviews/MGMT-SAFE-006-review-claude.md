# MGMT-SAFE-006 Review: command idempotency regression smoke

Reviewer: Claude
Date: 2026-05-15
Outcome: **APPROVED**

## Artifacts Reviewed

- `scripts/run_command_idempotency_regression.py`
- `scripts/test_run_command_idempotency_regression.py`
- `support/evidence/MGMT-SAFE-006/command-idempotency-regression.json`
- Commit: ca6787aa (3 task-owned files, clean)

## Verification Run by Reviewer

```
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/run_command_idempotency_regression.py scripts/test_run_command_idempotency_regression.py
→ PASS

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest scripts/test_run_command_idempotency_regression.py -v
→ 2 passed

PYTHONDONTWRITEBYTECODE=1 python3 scripts/run_command_idempotency_regression.py
→ 5/5 passed (status: passed)

git diff --check -- task-owned files
→ PASS
```

## Smoke Coverage (5/5 Passed)

1. **first-command-accepted**: POST /bff/v1/commands returns 202 with receipt_id.
2. **same-key-same-payload-replays**: Identical Idempotency-Key + payload replays the original receipt_id (idempotent replay correct).
3. **same-key-different-payload-conflicts**: Changed payload with same Idempotency-Key returns 409 with `IDEMPOTENCY_CONFLICT` code in both error and foundation_error layers, plus audit action `bff.command.idempotency_conflict`.
4. **body-idempotency-key-rejected**: Body-level `idempotencyKey` field rejected with 400 `INVALID_REQUEST` / `body_idempotency_key` precondition — route enforces header-only sourcing.
5. **single-durable-command-record**: Exactly one durable command record after accept+replay+conflict sequence; idempotency_record.status=succeeded, request_hash present, trace/correlation/request IDs captured, audit_action_type=bff.command.accepted.

## Safety Assessment

- `live_capital_side_effects: false` asserted in both summary and evidence.
- Uses isolated in-memory BFF test client; no live broker, no runtime mutation, no capital binding, no deployment side effects.
- State cleanup via context manager (`_isolated_bff_client`) restores original command_store and clears idempotency ledgers after each test.

## Findings

No blocking findings. The smoke is complete, correct, and traces the full idempotency contract (first-accept, replay, conflict, header-only enforcement, single durable record) against the BFF final-contract route.

Returning to Claude2 for finalization.
