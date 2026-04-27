# SD-FND-003 Codex Handoff

Task: `SD-FND-003`
Owner: Codex
Reviewer: Gemini
Status: ready for review

## Scope

Added shared foundation primitives for outbox, DLQ, schema registry, and
audited idempotent DLQ replay under `services/foundation`.

This implementation extends the SD-FND-001 package boundary without replacing
service-owned implementations. Existing telemetry ingest buffering, retry, DLQ,
and replay policy remain in `services/telemetry` and were not changed.

## Implemented Boundary

- `EventEnvelope`
- `OutboxRecord`
- `InboxReceipt`
- `JsonlOutboxStore`
- `DeadLetterEntry`
- `DeadLetterQueue`
- `SchemaRegistryEntry`
- `SchemaRegistry`
- `DeadLetterReplayProcessor`
- `IdempotentReplayLedger`

The public import surface is exported from `services/foundation/__init__.py`.
`services/foundation/README.md` now documents that the shared primitives are
storage-light record / JSONL helpers and that database, broker, and
domain-specific storage policies stay outside this package.

## Acceptance Evidence

- Shared outbox primitive exists with required event ordering fields:
  `event_id`, `aggregate_type`, `aggregate_id`, `sequence_no`,
  `causal_parent_id`, `trace_id`, `idempotency_key`, and payload.
- Shared DLQ primitive records rejected events with diagnostic tags, status, and
  optional append-only JSONL spill.
- Shared schema registry primitive registers versioned schema refs and validates
  payloads through a deterministic JSON-schema subset.
- Replay processor validates schema refs, uses an idempotency ledger, applies
  each event at most once, and emits `AuditAction` records for applied,
  duplicate-skipped, schema-rejected, and failed replay attempts.
- Replay proof covers duplicate DLQ entries for one event: only one side effect
  is applied, while both replay attempts produce audit actions.
- Telemetry storage semantics were not weakened; the existing telemetry replay
  policy tests still pass unchanged.

## Verification

```text
pytest services/foundation/tests -q
..........                                                               [100%]
10 passed in 0.23s

pytest services/telemetry/test_ingest_shock_absorption.py -q
.....................................................                    [100%]
53 passed in 4.08s
```

## Files

- `services/foundation/outbox.py`
- `services/foundation/dead_letter.py`
- `services/foundation/schema_registry.py`
- `services/foundation/replay.py`
- `services/foundation/__init__.py`
- `services/foundation/README.md`
- `services/foundation/tests/test_event_replay_primitives.py`

## Deferred

- BFF/runtime-manager command-path adoption remains `SD-FND-002` scope.
- Service-specific telemetry storage, runtime persistence, database-backed
  outbox stores, broker transports, and operational replay runbooks remain
  outside this foundation primitive task.
- No EP5 live/canary proof, research activation, or full-system completion claim
  is made by this task.
