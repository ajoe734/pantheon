# SD-FND-001 Codex Handoff

Task: `SD-FND-001`
Owner: Codex
Reviewer: Claude
Status: ready for review

## Scope

Materialized the canonical SD-00 / SD-12 shared foundation package boundary at
`services/foundation`.

The package contains side-effect-free Python primitives that can be imported by
BFF, runtime-manager, telemetry, governance, and tests without opening network,
database, broker, or framework clients.

## Implemented Boundary

- `TraceContext`
- `CommandEnvelope`
- `ErrorEnvelope`
- `IdempotencyRecord`
- `PolicyDecision`
- `AuditAction`
- `SecretRef`
- supporting refs/enums: `ActorRef`, `AuthorityScope`, `EnvironmentScope`
- deterministic helpers: canonical JSON, UTC timestamps, SHA-256 checksums,
  foundation ids

Path note: SD-00 already names `services/foundation`. SD-12's draft module list
used `services/foundations`; the implementation normalizes on the singular path
and documents that decision in `services/foundation/README.md`.

## Acceptance Evidence

- Package path documented: `services/foundation/README.md`
- Public exports defined: `services/foundation/__init__.py`
- Primitive tests added: `services/foundation/tests/test_primitives.py`
- SecretRef remains metadata-only and rejects frontend consumers plus obvious
  raw-secret metadata keys.
- Command envelopes bind actor, authority scope, trace, and idempotency key.
- Error envelopes cover validation and policy-denial shapes.
- Idempotency records canonicalize request hashes and expose status transitions.
- Audit actions carry trace/correlation refs and deterministic payload checksums.

## Verification

```text
pytest services/foundation/tests -q
........                                                                 [100%]
8 passed in 0.18s
```

## Deferred To Later SD-FND Tasks

- BFF/runtime-manager command-path adoption belongs to `SD-FND-002`.
- Durable outbox, DLQ, schema registry, replay, and persistence primitives belong
  to `SD-FND-003`.
- No production proof level, research activation, live/canary side effect, or
  full-system completion claim is made by this task.
