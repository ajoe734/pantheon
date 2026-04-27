# SD-FND-001 Claude Review

Task: `SD-FND-001` - Materialize canonical foundation package boundary
Owner: Codex
Reviewer: Claude
Status decision: APPROVE

## Scope Reviewed

The shared SD-00 / SD-12 foundation package boundary at `services/foundation`.
Per the materialization packet
(`docs/reviews/2026-04-27-sd-materializable-execution-task-packet.md`), this
task owns the package boundary and primitive contracts only; BFF / runtime
adoption is `SD-FND-002` and durable outbox / DLQ / schema-registry primitives
are `SD-FND-003`.

## Acceptance Verification

| Acceptance target | Evidence | Result |
|---|---|---|
| Foundation package path documented | `services/foundation/README.md` normalizes on singular `services/foundation` and notes the `services/foundations` draft wording in SD-12 | PASS |
| Side-effect-free imports | `services/foundation/__init__.py` re-exports pure value objects only; no network / db / framework clients are constructed at import time | PASS |
| `TraceContext` with trace / correlation / actor / environment | `services/foundation/envelopes.py:27-110`; covered by `test_trace_context_serializes_required_boundary_fields` | PASS |
| `CommandEnvelope` with actor, authority scope, trace, idempotency | `services/foundation/envelopes.py:113-202`; trace/idempotency mismatch rejected; covered by `test_command_envelope_*` | PASS |
| `ErrorEnvelope` covering validation and policy-denial shapes | `services/foundation/envelopes.py:205-292`; validation status `422`, policy denial status `403` with `policy_decision_ref`; covered by `test_error_envelope_supports_validation_and_policy_denial_shapes` | PASS |
| `IdempotencyRecord` with canonical request hash and status transitions | `services/foundation/idempotency.py:27-117`; covered by `test_idempotency_record_detects_duplicate_payloads_and_status_transitions` | PASS |
| `PolicyDecision` with reasons required for non-allow | `services/foundation/policy.py:22-110`; covered by `test_policy_decision_requires_reasons_for_denials` | PASS |
| `AuditAction` with trace refs and deterministic checksum | `services/foundation/audit.py:15-106`; covered by `test_audit_action_records_trace_refs_and_deterministic_checksum` | PASS |
| `SecretRef` metadata-only, blocks unsafe consumers and raw-secret keys | `services/foundation/secrets.py:61-116`; covered by `test_secret_ref_is_metadata_only_and_blocks_frontend_consumers` | PASS |
| Baseline primitive tests pass | `pytest services/foundation/tests -q` → `8 passed in 0.24s` (rerun on 2026-04-27 UTC during review) | PASS |

## Boundary And Scope

The package surface stays inside the boundary the materialization packet asked
for:

- pure value objects with deterministic JSON / SHA-256 helpers
  (`services/foundation/serialization.py`)
- structural validation via `FoundationValidationError` instead of network or
  storage I/O (`services/foundation/exceptions.py`)
- no broker, database, runtime-manager, or BFF adoption is mixed in
- no production proof level, research activation, live / canary side effect, or
  full-system completion claim is made
- `SecretRef` keeps secrets metadata-only by rejecting frontend / browser
  consumers and the obvious raw-secret metadata keys

## Observations (Non-Blocking)

These are notes for follow-up SD-FND tasks, not gate items:

- `policy.py` and `audit.py` import `field` from `dataclasses`; `policy.py`
  does not use it, while `audit.py` does. Trim unused imports during the next
  edit, but this does not affect contract behavior.
- `audit.py` derives `trace_id` and `correlation_id` from `TraceContext`; if a
  later evolution moves trace ids to a non-string shape, the audit primitive
  should follow.
- `CommandEnvelope.__post_init__` compares `self.trace.actor_ref.actor_ref` to
  `self.actor_ref.actor_ref`, which works correctly because `ActorRef.actor_ref`
  is the canonical "type:id" string property; future readers may want a more
  explicit method name when adoption begins in `SD-FND-002`.

None of the above blocks the package-boundary contract.

## Decision

Approve `SD-FND-001`.

The package boundary, primitive contracts, README, side-effect-free import
surface, and baseline tests match the acceptance shape declared in
`docs/reviews/2026-04-27-sd-materializable-execution-task-packet.md` for the
Foundations gap class. Adoption (`SD-FND-002`) and durable persistence
(`SD-FND-003`) remain the right places to advance from this baseline.

## Verification Reproduction

```text
PYTHONPATH=/home/edna/.local/lib/python3.12/site-packages:. \
  python3.12 -m pytest services/foundation/tests -q
........                                                                 [100%]
8 passed in 0.24s
```

## Handoff Back To Owner

Task returns to `Codex` for finalization to `done` per the standard
review_approved → done lifecycle.
