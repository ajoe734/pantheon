# PTJ-004 reviewer findings

Reviewer: Codex2
Disposition: approved

## Final re-review of `6ba87c2ca`

The prior fail-closed findings are resolved. Command-owner bodies now must
decode to JSON objects on both successful and HTTP error paths. Malformed 2xx
arrays and non-JSON HTTP error bodies are translated to
`DEPENDENCY_UNAVAILABLE`; neither can escape as an unhandled response or be
mistaken for a governed command receipt.

The durable command-owner delegation, atomic downstream idempotency, target and
transition rejection, and no-owner/unavailable behavior from the preceding
revision remain covered. PTJ-004 is approved for owner finalization.

Final verification:

```text
python3 -m pytest services/control-plane/bff/test_ptj_004_trade_journal.py \
  services/control-plane/bff/test_no_undefined_call_symbols.py \
  services/control-plane/bff/test_bff_error_envelope_shape.py -q

19 passed, 8 warnings
```

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

## Re-review of `5dfcd3989`

Disposition remains changes required.

The patch now validates target existence/state and replaces the process-local
dictionary with an fsync-backed JSONL admission record. That fixes restart
replay for a single BFF process, but it does not connect any route to the
reflection or persona-governance command owner. The JSONL record proves only
that the BFF admitted the request; retry, submit-review, and decide perform no
downstream command and capture no downstream acceptance receipt. Returning
`202 accepted` and `audit.durable: true` therefore still overstates the
governed command outcome.

The configured file is also not a safe multi-replica idempotency boundary:
`_LOCK` is process-local and the read/check/append sequence is not atomic
across BFF workers. Two workers can append duplicate records for the same
idempotency scope.

To clear the blocker, delegate to a configured durable command adapter/owner
that atomically owns idempotency and returns its receipt, and fail closed when
that adapter is absent or rejects the command. Tests must exercise adapter
invocation/rejection and durable replay (including concurrent admission), not
only local-file append/reload.

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
