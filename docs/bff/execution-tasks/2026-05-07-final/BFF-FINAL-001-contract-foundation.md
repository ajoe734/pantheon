# BFF-FINAL-001 - Contract Foundation

Priority: P0

Area: BFF models and error envelope

## Goal

Add the 2026-05-07 final contract primitives to Pantheon BFF without breaking existing operator command tests.

## Contract Inputs

- `ActionCommandStatus = "accepted" | "queued" | "completed"`
- Missing preconditions return non-2xx `BffErrorEnvelope`
- `CommandResponse<T>.data` is required
- `APPROVAL_REQUIRED` is a canonical error code

## Implementation Scope

Likely files:

- `services/control-plane/bff/models.py`
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/BFF_COMMAND_API_CONTRACT.md`
- `docs/conventions/BFF_RESPONSE_ENVELOPE.md`
- new or existing tests under `services/control-plane/bff/`

## Steps

1. Add final frontend-facing `BffErrorPayload` / `BffErrorEnvelope` models.
2. Add final `CommandResponse[T]` model with required `data`.
3. Add final `ActionCommandStatus` enum with only:
   - `accepted`
   - `queued`
   - `completed`
4. Add / map final error codes:
   - `CONFIRM_TOKEN_REQUIRED`
   - `APPROVAL_REQUIRED`
   - `TWO_MAN_REQUIRED`
   - `IDEMPOTENCY_CONFLICT`
   - `SSE_REPLAY_UNAVAILABLE`
5. Keep legacy `CommandSubmissionResponse` compatibility where existing operator command surfaces require it, but define the final response adapter explicitly.
6. Add contract tests that reject `requires_approval`, `requires_confirm_token`, and `requires_two_man` as success statuses.

## Acceptance Criteria

- Final response and error models are importable from BFF code.
- `APPROVAL_REQUIRED` is represented in BFF error codes.
- New tests prove final success statuses exclude every `requires_*` value.
- Existing BFF tests still pass.

## Verification

```bash
python -m pytest services/control-plane/bff/test_governance_command_submission.py -q
python -m pytest services/control-plane/bff -q
```

## Implementation Record

Implemented in `services/control-plane/bff/models.py` and
`services/control-plane/bff/main.py`:

- Added final `ActionCommandStatus`, `CommandResponse<T>`,
  `BffErrorPayload`, and `BffErrorEnvelope` primitives.
- Added canonical final error codes:
  `CONFIRM_TOKEN_REQUIRED`, `APPROVAL_REQUIRED`, `TWO_MAN_REQUIRED`,
  `IDEMPOTENCY_CONFLICT`, and `SSE_REPLAY_UNAVAILABLE`.
- Kept legacy `/api/v1/operator/commands` response compatibility while adding
  an explicit final command response adapter for new contract routes.
- Added regression tests in
  `services/control-plane/bff/test_final_contract_primitives.py` to prove
  final success statuses exclude `requires_*` precondition states and
  `CommandResponse<T>.data` is required.

Verification performed:

```bash
python3 -m pytest services/control-plane/bff/test_final_contract_primitives.py -q
python3 -m pytest services/control-plane/bff/test_governance_command_submission.py -q
python3 -m pytest services/control-plane/bff -q
```

Result: 382 BFF tests passed; pytest reported 32 existing
`datetime.utcnow()` deprecation warnings from `read_store.py`.

Closeout verification on 2026-05-07:

```bash
python3 -m pytest services/control-plane/bff/test_final_contract_primitives.py services/control-plane/bff/test_governance_command_submission.py -q
python3 -m pytest services/control-plane/bff -q
```

Result: 17 focused tests passed; 382 BFF tests passed with the same 32 existing
`datetime.utcnow()` deprecation warnings from `read_store.py`.
