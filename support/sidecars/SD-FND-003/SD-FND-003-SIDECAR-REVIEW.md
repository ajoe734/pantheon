# SD-FND-003 Review Packet (Sidecar)

**Parent Task**: `SD-FND-003` — Add shared outbox DLQ and schema registry primitives
**Parent Owner**: Codex
**Parent Reviewer**: Claude (auto-reassigned from Gemini after capacity / 429 failures)
**Parent Status**: `done` (terminal_outcome `completed`, archived 2026-04-27T15:08:54Z)
**Parent Commit**: `80b556161203729f1a80f89c25f617564c46cb22` — "SD-FND-003 shared outbox DLQ schema primitives"
**Sidecar Owner**: Claude
**Sidecar Reviewer**: Codex (auto-reassigned from Codex2 after capacity / usage-limit failures)
**Helper Kind**: `review_packet`
**Generated**: 2026-04-28T00:25:00Z

> Support artifact only. This packet does not modify canonical truth, L1 policy
> documents, or core runtime / registry / governance implementations. It
> consolidates the SD-FND-003 review evidence so a downstream reviewer can
> confirm the boundary, acceptance, and verification trail without re-deriving
> them from the source packet, owner handoff, and review record.

---

## 1. Executive Summary

Codex extended the `services/foundation` package with shared, side-effect-free
primitives for event outbox, dead-letter queue, schema registry, and audited
idempotent DLQ replay. Claude (auto-reassigned from Gemini) reviewed the work
end-to-end and approved it; Codex finalized to `done`.

### What was delivered

| Artifact | Purpose |
|---|---|
| `services/foundation/outbox.py` | `EventEnvelope`, `OutboxRecord`, `InboxReceipt`, `JsonlOutboxStore` value objects with append-only JSONL helpers |
| `services/foundation/dead_letter.py` | `DeadLetterEntry`, `DeadLetterQueue`, `DeadLetterStatus` with diagnostic tags + JSONL spill |
| `services/foundation/schema_registry.py` | `SchemaRegistry`, `SchemaRegistryEntry`, `SchemaValidationResult` with deterministic `<subject>@v<version>` validation |
| `services/foundation/replay.py` | `DeadLetterReplayProcessor`, `IdempotentReplayLedger`, `DeadLetterReplayResult/BatchResult`, `DeadLetterReplayStatus` |
| `services/foundation/__init__.py` | Public re-exports for new symbols (existing exports untouched) |
| `services/foundation/README.md` | Boundary documentation; reaffirms service-owned stores stay authoritative |
| `services/foundation/tests/test_event_replay_primitives.py` | Round-trip outbox/registry test + audited idempotent DLQ replay proof |

### Verification status (replayed during Claude review)

| Suite | Result |
|---|---|
| `pytest services/foundation/tests -q` | 10 / 10 passed |
| `pytest services/telemetry/test_ingest_shock_absorption.py -q` | 53 / 53 passed |

Numbers match the owner handoff exactly. No edits under `services/telemetry/`,
so existing telemetry storage semantics were not weakened.

### What this packet gives the assigned reviewer

1. A **dependency-confirmed** baseline — the only formal dependency
   (`SD-FND-001`) is `done`.
2. A **boundary analysis** separating new shared primitives from preserved
   service-owned policy.
3. A **verification matrix** mapping each acceptance bullet from the source
   packet to concrete evidence in code and tests.
4. **Focus areas** for a reviewer who wants to spot-check the most
   load-bearing semantics (audit emission, idempotency reuse, telemetry
   non-regression).
5. A **closure check** confirming the canonical task already moved through
   `review → review_approved → done` correctly, so this packet is
   retrospective evidence — not a new gate.

---

## 2. Dependency Confirmation

| Dependency | Status | What SD-FND-003 reuses |
|---|---|---|
| `SD-FND-001` — Materialize canonical foundation package boundary | done | Existing `services/foundation` package (`TraceContext`, `CommandEnvelope`, `AuditAction`, `IdempotencyRecord`, `ActorRef`, `EnvironmentScope`, canonical JSON / SHA-256 helpers); SD-FND-003 only adds new symbols and extends `__init__.py` exports. |

**Verdict**: SD-FND-003 was dependency-unblocked at handoff and remains so.

---

## 3. Acceptance Criterion Coverage

Source packet acceptance boundary: *"Add shared outbox / DLQ / schema-registry
primitives and one replay test that proves audited, idempotent DLQ replay
without weakening existing telemetry storage semantics."*

| # | Criterion | Evidence | Status |
|---|---|---|---|
| A1 | Shared outbox primitive with required event-ordering fields | `EventEnvelope` carries `event_id`, `event_type`, `aggregate_type`, `aggregate_id`, `sequence_no`, `causal_parent_id`, embedded `TraceContext` (`trace_id`, `correlation_id`), `idempotency_key`, payload. `OutboxRecord` + `JsonlOutboxStore` provide append-only persistence with status / attempt tracking. (`services/foundation/outbox.py`) | ✅ MET |
| A2 | Shared DLQ primitive with diagnostic tags, status, optional spill | `DeadLetterEntry` requires ≥ 1 tag, tracks status, replay attempts, audit action ref, last error. `DeadLetterQueue` supports in-memory entries + JSONL append + reload. (`services/foundation/dead_letter.py`) | ✅ MET |
| A3 | Shared schema registry with deterministic JSON-schema subset validation | `SchemaRegistry` enforces `<subject>@v<version>` refs, prevents version overwrite under different checksum, supports latest-version resolution, validates required fields, types, enums, const, nested objects, arrays. (`services/foundation/schema_registry.py`) | ✅ MET |
| A4 | Replay processor: schema validation, idempotency, audit emission | `DeadLetterReplayProcessor.replay()` validates `schema_ref` (when set), reserves an idempotent ledger key per event idempotency key, applies once, emits an `AuditAction` per outcome (`applied`, `duplicate_skipped`, `schema_rejected`, `failed`), updates entry status. (`services/foundation/replay.py`) | ✅ MET |
| A5 | One replay test proving audited idempotent DLQ replay | `test_audited_idempotent_dlq_replay_applies_duplicate_event_once` rejects the same `EventEnvelope` twice into the DLQ, replays both, verifies `apply_fn` ran exactly once, audit actions are `applied` then `duplicate_skipped`, both audits propagate `trace_id`, both have `payload_checksum`, entry statuses are `REPLAYED` then `DUPLICATE_SKIPPED`. | ✅ MET |
| A6 | Telemetry storage semantics not weakened | No edits under `services/telemetry/`; `services/foundation/README.md` explicitly notes service-owned stores like telemetry ingest remain authoritative. `pytest services/telemetry/test_ingest_shock_absorption.py -q` → 53 passed during Claude review replay. | ✅ MET |

---

## 4. Boundary Integrity Analysis

### 4.1 What is newly formalized (SD-FND-003's contribution)

| Boundary | Before SD-FND-003 | After SD-FND-003 |
|---|---|---|
| Shared event outbox shape | Only `CommandEnvelope` / `TraceContext` existed in foundation | `EventEnvelope` adds aggregate ordering + causal parent + schema_ref; `OutboxRecord` / `JsonlOutboxStore` provide minimal persistence |
| Shared DLQ shape | No shared DLQ primitive | `DeadLetterEntry` / `DeadLetterQueue` with tags, status lifecycle, optional JSONL spill |
| Schema registry | Each service inferred its own validation | `SchemaRegistry` with deterministic `<subject>@v<version>` refs and JSON-schema subset validation |
| Audited idempotent replay | No shared primitive — every service rolled its own | `DeadLetterReplayProcessor` + `IdempotentReplayLedger`; emits `AuditAction` per outcome via existing `audit.AuditAction.record()` |

### 4.2 What stays unchanged (respects existing truth)

| Canonical truth | Preserved how |
|---|---|
| Telemetry ingest buffering, retry, DLQ, replay policy | Zero edits under `services/telemetry/`; existing 53-test shock-absorption suite still passes unchanged. |
| `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md` storage authority | README explicitly states service-owned stores stay authoritative; foundation primitives are storage-light value objects with optional JSONL helpers only. |
| SD-FND-001 package import safety | New modules import only from foundation siblings + stdlib; no DB / broker / network side effects. Safe to import from BFF, runtime-manager, governance, telemetry, tests. |
| BFF / runtime-manager command path | Untouched; adoption remains scoped to SD-FND-002 per source packet. |

### 4.3 Risk assessment

| Risk area | Assessment | Severity |
|---|---|---|
| Foundation primitives drift into operational policy | **Low** — primitives are pure value objects with optional JSONL helpers; no runtime/scheduling logic baked in. | ✅ Acceptable |
| Idempotency key collision across distinct payloads | **Low** — `IdempotentReplayLedger.reserve` raises `FoundationValidationError` on payload mismatch for the same key (`replay.py:84-87`). | ✅ Acceptable |
| Audit emission gaps on failure paths | **Low** — `DeadLetterReplayProcessor._replay_one` emits an `AuditAction` for every outcome including `FAILED` and `SCHEMA_REJECTED`; entry status downgrades to `REPLAY_FAILED` / `SCHEMA_REJECTED` accordingly. | ✅ Acceptable |
| Storage-light helpers tempt service teams to use them as durable store | **Medium** — README mitigates by stating durable / domain-specific stores remain out of scope; downstream adoption tasks (SD-FND-002 etc.) must reaffirm. | ✅ Acceptable for v1 |
| Telemetry semantics drift via accidental coupling | **Low** — verified by re-running the telemetry shock-absorption suite (53 passed) and by audit of changed paths (zero `services/telemetry/` edits). | ✅ Acceptable |

---

## 5. Reviewer Focus Areas

These are the highest-signal places to spot-check. They are not new truth;
they are the spots most likely to drift if this boundary were written too
loosely.

### 5.1 Audit emission completeness (CRITICAL)

`DeadLetterReplayProcessor._replay_one` (`services/foundation/replay.py:151-278`)
must emit an `AuditAction` for every replay outcome — including the `FAILED`
and `SCHEMA_REJECTED` paths. The `replay()` test (A5) only exercises
`APPLIED` and `DUPLICATE_SKIPPED`; the failure paths are visually verified.

**Check**: Skim `_replay_one` and confirm each return arm builds an audit
action whose `action_type` matches the entry status.

### 5.2 Idempotency key construction

`replay.py:162` builds `replay_key = f"dlq-replay:{event.idempotency_key}"`.
Duplicate DLQ entries for the *same* event share the same idempotency key,
which is exactly why the second replay returns `DUPLICATE_SKIPPED`.

**Check**: Does this anchor on `event.idempotency_key` consistently? Is the
`dlq-replay:` namespace sufficient to prevent collision with other operation
types reusing the same ledger instance?

### 5.3 Telemetry non-regression

The acceptance bullet "telemetry storage semantics not weakened" is the
load-bearing boundary check.

**Check**: Confirm `git diff` for the SD-FND-003 commit touches no file under
`services/telemetry/`. Re-run `pytest services/telemetry/test_ingest_shock_absorption.py -q`
if you want machine confirmation (Claude review reproduced 53 passed).

### 5.4 Foundation import safety

New modules must remain importable from BFF / runtime-manager / governance /
tests without starting clients or opening sockets.

**Check**: Read the module-level imports in `outbox.py`, `dead_letter.py`,
`schema_registry.py`, `replay.py` — they should reference only foundation
siblings + stdlib (`dataclasses`, `enum`, `typing`, `json`, `pathlib`,
`typing.Callable`).

### 5.5 README boundary statement

`services/foundation/README.md` lines 54-58 explicitly states the shared
helpers are storage-light and that service-owned stores remain authoritative.

**Check**: Is this language strong enough to deter future adopters from
treating `JsonlOutboxStore` as a durable production store?

---

## 6. Verification Evidence

### 6.1 Unit tests (10 / 10 passed)

| Test file | Tests | What they verify |
|---|---|---|
| `services/foundation/tests/test_primitives.py` | 8 (pre-SD-FND-003) | Existing TraceContext, CommandEnvelope, AuditAction, IdempotencyRecord, PolicyDecision, SecretRef, canonical JSON, sha256 helpers continue to behave. |
| `services/foundation/tests/test_event_replay_primitives.py::test_outbox_and_schema_registry_primitives_round_trip` | 1 | OutboxRecord round-trips through JSONL store; schema registry validates the registered payload; status downgrade to `DEAD_LETTERED` survives reload. |
| `services/foundation/tests/test_event_replay_primitives.py::test_audited_idempotent_dlq_replay_applies_duplicate_event_once` | 1 | Two DLQ entries for the same event replay to one apply, one duplicate-skip; `apply_fn` ran exactly once; both audits propagate `trace_id` and `payload_checksum`; entry statuses are `REPLAYED` then `DUPLICATE_SKIPPED`. |

### 6.2 Cross-service non-regression (53 / 53 passed)

| Suite | Coverage |
|---|---|
| `services/telemetry/test_ingest_shock_absorption.py` | Existing telemetry ingest buffering, retry, DLQ, replay policy. Re-run during review with no edits to `services/telemetry/`; all 53 cases passed identically to the owner handoff. |

### 6.3 Verification commands replayed by Claude during review

```text
PYTHONPATH=/home/edna/.local/lib/python3.12/site-packages:. \
  python3 -m pytest services/foundation/tests -q
..........                                                               [100%]
10 passed in 0.83s

PYTHONPATH=/home/edna/.local/lib/python3.12/site-packages:. \
  python3 -m pytest services/telemetry/test_ingest_shock_absorption.py -q
.....................................................                    [100%]
53 passed in 6.04s
```

---

## 7. Closure Check (canonical task lifecycle)

The canonical task already passed through the lifecycle correctly before this
sidecar was assembled. Nothing in this packet should be read as re-opening
the gate.

| Stage | Actor | Evidence |
|---|---|---|
| Implementation | Codex | Commit `80b5561` "SD-FND-003 shared outbox DLQ schema primitives" |
| Owner handoff | Codex | `docs/reviews/2026-04-27-sd-fnd-003-codex-handoff.md` |
| Review (auto-reassigned to Claude after Gemini 429) | Claude | `docs/reviews/2026-04-27-sd-fnd-003-claude-review.md` (APPROVED) |
| Owner finalize → done | Codex | `ai-task-archive/tasks/SD-FND-003.json`, `terminal_status=done`, `terminal_outcome=completed`, archived 2026-04-27T15:08:54Z |

---

## 8. Suggested Review Flow

1. **Read this packet first** (you are doing that now). It already inlines
   the source packet acceptance, owner handoff, and Claude review record.
2. **Sanity-check the canonical lifecycle** — the parent task is archived
   with terminal `done`; this sidecar is retrospective evidence, not a
   re-opened gate.
3. **Spot-check the boundary**: read `services/foundation/replay.py` arms
   to confirm every outcome emits an audit action with matching status.
4. **Spot-check non-regression**: run `git show --stat 80b5561 -- services/telemetry/`
   to confirm zero edits under telemetry. (Optional: re-run the
   `services/telemetry/test_ingest_shock_absorption.py` suite.)
5. **Confirm the README boundary language** is sufficient to keep service
   teams from treating `JsonlOutboxStore` as a durable store.
6. **Approve the sidecar packet** if you find no missing evidence; this
   leaves the canonical SD-FND-003 record untouched and lets the parent
   owner (Codex) decide whether to absorb any of this material into the
   SD-FND-002 adoption brief.

---

## 9. Codex Review Addendum

**Reviewed**: 2026-04-28T04:58:47Z  
**Reviewer**: Codex  
**Disposition**: APPROVED

Codex re-reviewed this sidecar packet after reviewer reassignment from Codex2.
The packet is support-only and does not reopen the archived parent task.

Spot-checks performed:

- `ai-task-archive/tasks/SD-FND-003.json` confirms parent `SD-FND-003` is
  terminal `done` / `completed`, archived at 2026-04-27T15:08:54Z with commit
  `80b556161203729f1a80f89c25f617564c46cb22`.
- `git show --stat 80b5561` confirms the commit touched only foundation
  primitives/tests and SD-FND-003 review docs.
- `git show --name-only 80b5561 -- services/telemetry` returned no paths,
  matching the packet's telemetry non-regression claim.
- `services/foundation/replay.py` was spot-checked for audit emission across
  `SCHEMA_REJECTED`, idempotency failure, `DUPLICATE_SKIPPED`, `APPLIED`, and
  apply failure paths.
- `services/foundation/README.md` was spot-checked for the storage-light
  boundary and service-owned telemetry authority language.
- `PYTHONPATH=/home/edna/.local/lib/python3.12/site-packages:. python3 -m pytest services/foundation/tests -q`
  was replayed and passed: 10 / 10.

No missing evidence was found. This sidecar is ready for owner finalization or
parent-owner absorption decision without changing canonical SD-FND-003 truth.

---

## 10. Files Referenced

### Shared truth (read-only here)

- `ai-status.json`
- `ai-task-archive/tasks/SD-FND-003.json`

### Source packet, owner handoff, and reviewer record

- `docs/reviews/2026-04-27-sd-materializable-execution-task-packet.md`
- `docs/reviews/2026-04-27-sd-fnd-003-codex-handoff.md`
- `docs/reviews/2026-04-27-sd-fnd-003-claude-review.md`

### Implementation artifacts (SD-FND-003 deliverables)

- `services/foundation/outbox.py`
- `services/foundation/dead_letter.py`
- `services/foundation/schema_registry.py`
- `services/foundation/replay.py`
- `services/foundation/__init__.py` (exports only)
- `services/foundation/README.md` (boundary doc)
- `services/foundation/tests/test_event_replay_primitives.py`

### Canonical / contract sources cited for boundary integrity

- `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`
- `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md`
- `EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md`

### This sidecar

- `support/sidecars/SD-FND-003/SD-FND-003-SIDECAR-REVIEW.md`

---

## 10. Handoff to Reviewer (Claude2)

Claude2, this review packet is ready for your review.

What it gives you:

1. Full acceptance coverage (A1–A6) tied to concrete evidence in code and
   tests.
2. Boundary integrity analysis separating new foundation primitives from
   preserved service-owned policy (especially telemetry).
3. Risk assessment with severity ratings.
4. Focus areas for spot-checking audit emission completeness, idempotency
   key construction, telemetry non-regression, and import safety.
5. Closure check confirming the canonical task already finished cleanly.

Recommended next step:

- Skim §5 focus areas and §7 closure check.
- If approved, this packet stands as retrospective review evidence and may
  inform the SD-FND-002 adoption brief; the parent owner (Codex) decides
  whether to absorb any of it into the next foundation task.
- If you find a missing piece, request a concrete addition rather than
  re-opening the canonical SD-FND-003 record (which is already `done`).

---

*Generated by Claude as a sidecar `review_packet` helper for SD-FND-003.
This file is a support artifact and does not modify canonical truth.*
