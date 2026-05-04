# SD-FND-003 Claude Review

Task: `SD-FND-003`
Owner: Codex
Reviewer: Claude (auto-reassigned from Gemini after Gemini capacity/429)
Status: review → review_approved
Source packet: `docs/reviews/2026-04-27-sd-materializable-execution-task-packet.md`
Owner handoff: `docs/reviews/2026-04-27-sd-fnd-003-codex-handoff.md`

## Acceptance Criteria

Per packet acceptance boundary for SD-FND-003: "Add shared outbox / DLQ /
schema-registry primitives and one replay test that proves audited, idempotent
DLQ replay without weakening existing telemetry storage semantics."

| Criterion | Result |
|---|---|
| Shared outbox primitive with required event-ordering fields | Met. `EventEnvelope` (`services/foundation/outbox.py`) carries `event_id`, `event_type`, `aggregate_type`, `aggregate_id`, `sequence_no`, `causal_parent_id`, embedded `TraceContext` (`trace_id`, `correlation_id`), `idempotency_key`, and payload. `OutboxRecord` and `JsonlOutboxStore` give append-only persistence with status / attempt tracking. |
| Shared DLQ primitive with diagnostic tags, status, optional spill | Met. `DeadLetterEntry` requires at least one tag, tracks status, replay attempts, audit action ref, last error. `DeadLetterQueue` supports in-memory entries, JSONL spill append, and reload. |
| Shared schema registry with deterministic JSON-schema subset validation | Met. `SchemaRegistry` enforces `<subject>@v<version>` refs, prevents version overwrite under different checksum, supports latest-version resolution, and validates required fields, types, enums, const, nested objects, and arrays. |
| Replay processor: schema validation, idempotency, audit emission | Met. `DeadLetterReplayProcessor` validates `schema_ref` (when set), reserves an idempotent ledger key per event idempotency key, applies once, emits an `AuditAction` per outcome (`applied`, `duplicate_skipped`, `schema_rejected`, `failed`), and updates entry status. |
| One replay test proving audited idempotent DLQ replay | Met. `services/foundation/tests/test_event_replay_primitives.py::test_audited_idempotent_dlq_replay_applies_duplicate_event_once` rejects the same `EventEnvelope` twice into the DLQ, replays both, and verifies `apply_fn` ran exactly once, audit actions are `applied` then `duplicate_skipped`, both audits propagate `trace_id`, both have `payload_checksum`, and entry statuses are `REPLAYED` then `DUPLICATE_SKIPPED`. |
| Telemetry storage semantics not weakened | Met. No edits under `services/telemetry/`; `services/foundation/README.md` explicitly notes service-owned stores like telemetry ingest remain authoritative. `pytest services/telemetry/test_ingest_shock_absorption.py -q` → 53 passed. |

## Files Reviewed

Modified:

- `services/foundation/__init__.py` — exports new primitives only; existing
  symbols unchanged.
- `services/foundation/README.md` — documents new in-scope helpers and
  re-states out-of-scope durable storage / domain policy.

Added:

- `services/foundation/outbox.py`
- `services/foundation/dead_letter.py`
- `services/foundation/schema_registry.py`
- `services/foundation/replay.py`
- `services/foundation/tests/test_event_replay_primitives.py`

No edits outside `services/foundation/`. SD-FND-002 BFF / runtime-manager
adoption is correctly deferred.

## Verification Replay (this review)

```text
PYTHONPATH=/home/lupin/.local/lib/python3.12/site-packages:. \
  python3 -m pytest services/foundation/tests -q
..........                                                               [100%]
10 passed in 0.83s

PYTHONPATH=/home/lupin/.local/lib/python3.12/site-packages:. \
  python3 -m pytest services/telemetry/test_ingest_shock_absorption.py -q
.....................................................                    [100%]
53 passed in 6.04s
```

Numbers match the owner handoff (10 / 53). System pytest at `/home/lupin/.local`
required PYTHONPATH override; underlying pytest 9.0.3 is the same version the
owner used.

## Observations

- Primitives are pure value objects with optional JSONL helpers — no DB,
  broker, or network side effects. Safe to import from BFF, runtime-manager,
  governance, and tests.
- `EventEnvelope.new` derives a deterministic `idempotency_key` when not
  supplied, anchoring downstream replay safety in payload + identity.
- `IdempotentReplayLedger.reserve` rejects payload mismatch on the same key,
  protecting against replay-key collisions across distinct payloads.
- Replay processor records audit actions even when apply_fn raises, with
  ledger marked failed and entry status `REPLAY_FAILED` — failures are
  observable.
- README explicitly preserves telemetry ingest authority. Boundary is
  consistent with `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md` intent.

## Disposition

APPROVED. Acceptance boundary in the source packet is fully covered, owner
handoff evidence reproduced, and existing telemetry ingest semantics remain
intact. Returning to Codex for owner finalization to `done`.
