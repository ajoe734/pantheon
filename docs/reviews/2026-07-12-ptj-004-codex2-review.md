# PTJ-004 reviewer findings

Reviewer: Codex2  
Disposition: changes required

## Blocking finding

The three governed command routes are not connected to a durable command or
audit owner. `trade_journal.command()` records accepted receipts only in the
process-local `_RECEIPTS` dictionary and returns `202` without validating that
the referenced episode or lesson exists or that a downstream command accepted
the operation. A BFF restart loses both the idempotency record and the claimed
audit receipt. Consequently, an arbitrary nonexistent resource currently
receives an `accepted`/`audit: true` response, and downstream unavailability is
never surfaced for command paths.

Required changes:

- Delegate retry, submit-review, and decide to the appropriate durable
  reflection/persona governance command boundary, or fail closed with
  `DEPENDENCY_UNAVAILABLE` when that boundary is not configured.
- Persist idempotency and audit evidence at that owning boundary; do not claim
  durable audit from BFF process memory.
- Validate the target resource and allowed transition before returning an
  accepted receipt.
- Add tests proving nonexistent-resource rejection, downstream-unavailable
  behavior for POST routes, durable/idempotent delegation, and no false audit
  claim.

## Verification

The submitted focused suite passes but does not cover the blocking behavior:

```text
python3 -m pytest services/control-plane/bff/test_ptj_004_trade_journal.py \
  services/control-plane/bff/test_no_undefined_call_symbols.py \
  services/control-plane/bff/test_bff_error_envelope_shape.py -q

14 passed, 8 warnings
```

Read APIs, RBAC, masking, filtering, and explicit missing projection handling
look directionally consistent with this task's BFF boundary. Approval remains
blocked on governed-command truthfulness and durability.
