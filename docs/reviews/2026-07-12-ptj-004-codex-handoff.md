# PTJ-004 owner handoff

Owner: Codex  
Reviewer: Codex2  
Status: review approved; ready for owner finalization

## Delivered boundary

- Adds persona-scoped journal list/detail, reflection inbox, and pattern read APIs.
- Reads telemetry/persona-owned projection files configured by environment; the BFF does not become order, fill, P&L, reflection, or memory truth.
- Adds environment/filter/cursor handling, explicit coverage/source metadata, viewer masking, read/write RBAC, and cross-persona denial.
- Adds governed retry, submit-review, and decide commands requiring a reason and `Idempotency-Key`; the BFF delegates them to the configured durable command owner and returns only its accepted receipt and audit reference.
- The durable owner, rather than BFF memory or a local JSONL admission log, atomically owns target/transition validation, idempotency, command acceptance, and audit evidence. An absent, unreachable, rejecting, or malformed owner fails closed.
- Missing projection dependencies fail explicitly with `DEPENDENCY_UNAVAILABLE`; no synthetic facts or reflections are generated.

## Routes

- `GET /bff/personas/{persona_id}/trade-journal`
- `GET /bff/personas/{persona_id}/trade-journal/{trade_episode_id}`
- `GET /bff/personas/{persona_id}/trade-reflections`
- `GET /bff/personas/{persona_id}/trade-patterns`
- `POST /bff/personas/{persona_id}/trade-journal/{episode_id}/reflection:retry`
- `POST /bff/personas/{persona_id}/trade-lessons/{lesson_id}:submit-review`
- `POST /bff/personas/{persona_id}/trade-lessons/{lesson_id}:decide`

## Verification

```text
python3 -m pytest services/control-plane/bff/test_ptj_004_trade_journal.py \
  services/control-plane/bff/test_no_undefined_call_symbols.py \
  services/control-plane/bff/test_bff_error_envelope_shape.py -q

19 passed, 8 warnings
```

The focused contract covers complete/partial/unavailable reads, pagination and environment filtering,
401/403, viewer masking, cross-persona denial, downstream rejection, owner-managed duplicate/conflicting
POST requests, concurrent same-key admission, immutable retry facts reference propagation, and all three
governed commands. PTJ-006 remains responsible for the frontend;
PTJ-007 remains responsible for hosted cross-service acceptance.
