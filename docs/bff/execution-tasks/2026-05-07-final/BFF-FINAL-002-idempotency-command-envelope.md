# BFF-FINAL-002 - Idempotency and Command Envelope

Priority: P0

Depends on: BFF-FINAL-001

Area: command admission

## Goal

Align BFF command admission with the final frontend contract while preserving the existing governed command facade.

## Contract Inputs

- All write/action endpoints receive idempotency through HTTP `Idempotency-Key`
- Business request bodies must not contain `idempotencyKey`
- Success responses use `CommandResponse<T>` with required `data`

## Implementation Scope

Likely files:

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/command_queue.py`
- `services/control-plane/bff/command_executor.py`
- `services/control-plane/bff/models.py`
- `services/control-plane/bff/test_governance_command_submission.py`
- `services/control-plane/bff/BFF_COMMAND_API_CONTRACT.md`

## Steps

1. Accept `Idempotency-Key` on every final write/action route.
2. Decide whether to keep `X-Idempotency-Key` as a temporary compatibility alias; if kept, document precedence.
3. Reject request bodies containing `idempotencyKey` on final contract routes.
4. Persist idempotency records using the canonical header value.
5. Return identical command response on duplicate same-key/same-body replay.
6. Return non-2xx `BffErrorEnvelope` with `IDEMPOTENCY_CONFLICT` for same-key/different-body.
7. Wrap accepted/queued/completed command admissions in final `CommandResponse<T>` where the route is part of the new `/bff/...` contract.

## Acceptance Criteria

- `Idempotency-Key` works for final endpoints.
- Body-level `idempotencyKey` is rejected on final endpoints.
- Replay and conflict behavior are covered by tests.
- Existing `/api/v1/operator/commands` behavior remains compatible or has an explicit migration test.

## Verification

```bash
python -m pytest services/control-plane/bff/test_governance_command_submission.py -q
python -m pytest services/control-plane/bff/test_command_executor.py -q
```
