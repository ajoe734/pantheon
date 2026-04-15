# BP5-SVC-009 Acceptance Packet

**Sidecar kind:** `acceptance_packet`  
**Sidecar task:** `BP5-SVC-009-SIDECAR-ACCEPTANCE`  
**Helper parent:** `BP5-SVC-009` - Realize telemetry ingest service and shock-absorption path  
**Parent owner:** `Claude`  
**Parent reviewer:** `Codex`  
**Prepared by:** `Codex`  
**Reviewer:** `Claude`  
**Date:** `2026-04-15`  
**Status:** `review_approved`

> Scope constraint: support artifact only. This packet does not modify L1 canonical truth, runtime
> implementation, registry truth, or governance truth. It records live repo evidence from the
> current telemetry worktree so the assigned reviewer can judge the helper slice quickly and give
> the parent owner a compact acceptance scaffold.
>
> Review status update: `Claude` approved this sidecar on `2026-04-15` in
> `.coordination/reviews/BP5-SVC-009-SIDECAR-ACCEPTANCE-review.md`. The three REVIEW callouts in
> this packet remain open parent-task acceptance questions for `BP5-SVC-009`; they were not
> resolved by this sidecar.

---

## 1. Purpose

This packet gives `Claude` a compact review surface for `BP5-SVC-009-SIDECAR-ACCEPTANCE`:

1. a criterion-by-criterion acceptance checklist for the parent telemetry ingest slice
2. a live worktree inventory of the telemetry ingest and shock-absorption surfaces already present
3. fresh runnable evidence from the current smoke and unit suites
4. a dependency map showing what this parent task unblocks and which semantic gaps still deserve explicit judgment

The key point is narrow: **the telemetry ingest shock-absorption scaffold is materially present and
re-runnable, but the current repo state still shows three material acceptance gaps before anyone
should treat the parent task as fully closed: authoritative RuntimeBindingStore validation is not
wired, the default writer is still a no-op test sink instead of canonical Postgres persistence, and
the retry/replay path does not yet show explicit idempotent dedupe behavior.**

---

## 2. Acceptance Checklist

Formal acceptance criteria from the phase-5 planning session:

- AC-1: `telemetry service writes canonical stage and runtime-binding references`
- AC-2: `high-volume ingest uses explicit buffering, replay, and idempotent batch-write behavior`

### AC-1: Telemetry service writes canonical stage and runtime-binding references

| Check | Evidence | Status |
|---|---|---|
| Canonical telemetry envelope requires runtime-binding and deployment-stage references | `services/telemetry/telemetry_event.schema.json:7-22,24-117` requires `binding_id`, `runtime_id`, `capital_pool_id`, `artifact_id`, `artifact_version`, `deployment_stage`, `plan_id`, `persona_capital_binding_id`, and rollback lineage fields | PASS |
| Producer-side capture code injects those references into emitted events | `services/telemetry/capture.py:574-666` injects binding context and normalizes `environment` / `execution_mode` aliases; `services/telemetry/test_capture.py:390-466` covers binding/stage injection and alias semantics | PASS |
| Fresh producer-evidence tests re-run cleanly | `python3 -m unittest test_capture.TestBindingStageEvidence` from `services/telemetry/` returned `Ran 13 tests ... OK` on `2026-04-15` | PASS |
| Ingest layer rejects events that omit binding/governance evidence | `services/telemetry/ingest_svc.py:193-224` validates binding identity, deployment stage enum, plan id, persona capital binding id, and rollback field pairing; `services/telemetry/test_ingest_shock_absorption.py:618-636` covers missing `binding_id` rejection | PASS |
| Ingest layer resolves `binding_id` against RuntimeBinding truth and proves stage/artifact/runtime match the binding snapshot | `services/telemetry/TEL_001A_FIELD_PACKET.md:134-145,187-191` says ingest must resolve `binding_id` against `RuntimeBindingStore`, validate `deployment_stage`/artifact/runtime/temporal window, and normalize stage from binding truth. `services/telemetry/ingest_svc.py:193-224` currently validates only the event's self-reported fields and does not perform store lookup or temporal-window checks | REVIEW |
| Ingest layer injects `trace_id` when missing and performs event normalization before enqueue | `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md:82-89` and `services/telemetry/TEL_001A_FIELD_PACKET.md:136-145` call for `trace_id` injection and ingest-time normalization, but no such logic appears in `services/telemetry/ingest_svc.py:193-292` | REVIEW |
| Default service wiring persists canonical refs into Postgres rather than a test sink | `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md:30-54,109-186` defines `telemetry-ingest-svc -> durable buffer -> batch writer -> Postgres canonical telemetry` as the formal path. `services/telemetry/ingest_svc.py:143-155,330-337` still defaults to `_default_write_fn`, which is explicitly a memory-only no-op, and repo search shows no production instantiation of `TelemetryIngestService` with a real Postgres writer | REVIEW |

**AC-1 assessment:** the repo now has a canonical telemetry envelope, producer-side binding/stage
field injection, and ingest-time evidence validation. It does **not** yet show authoritative
RuntimeBindingStore resolution or a default canonical Postgres write path, so reviewer judgment
should treat AC-1 as materially advanced but not obviously complete.

### AC-2: High-volume ingest uses explicit buffering, replay, and idempotent batch-write behavior

| Check | Evidence | Status |
|---|---|---|
| Explicit shock-absorption components exist in code | `services/telemetry/buffer.py`, `services/telemetry/batch_writer.py`, `services/telemetry/backpressure.py`, and `services/telemetry/dead_letter.py` implement buffer, batch writer, backpressure, and DLQ layers; `services/telemetry/ingest_svc.py:1-20` ties them together | PASS |
| Batch writer implements partition routing, retry, and DLQ routing | `services/telemetry/batch_writer.py:241-337` groups by partition key, retries with exponential backoff, and routes poison or exhausted batches into the DLQ | PASS |
| High-volume ingest smoke passes in the current worktree | `python3 services/telemetry/smoke_test_ingest.py` returned `ALL SMOKE TESTS PASSED`, including `1000` ingested and `1000` written events | PASS |
| Smoke covers backpressure and DLQ replay paths | `services/telemetry/smoke_test_ingest.py:89-160` verifies pressure transitions, delay rules, DLQ capture, and replay counts | PASS |
| Unit coverage exercises end-to-end ingest, overflow, batch ingest, replay, and stats | `services/telemetry/test_ingest_shock_absorption.py:580-724`; `python3 -m unittest services.telemetry.test_ingest_shock_absorption` returned `Ran 39 tests in 3.619s ... OK` | PASS |
| Default buffer path is durable enough for crash recovery | `services/telemetry/buffer.py:95-103` explicitly documents the default `InMemoryBuffer` as **not durable** and lossy on process crash; `services/telemetry/BUFFER_CHOICE_ADR.md:77-111` treats it as a development shim until Redis Streams or another durable stream is activated | REVIEW |
| Batch-write path is explicitly idempotent under at-least-once delivery | `EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md:37-42,115-137` requires consumer idempotency and replay without duplicated side effects. `services/telemetry/telemetry_event.schema.json:24-28` calls `event_id` the primary idempotency key, but `services/telemetry/batch_writer.py:257-337` retries entire batches without visible dedupe by `event_id` / `idempotency_key`, and the smoke/unit suites do not assert duplicate suppression | REVIEW |
| Replay path prevents duplicate side effects when DLQ entries are re-enqueued | `services/telemetry/ingest_svc.py:365-376` replays entries straight back into the buffer; current tests check replay count, not duplicate-write suppression or a dry-run/rebuild mode | REVIEW |

**AC-2 assessment:** explicit buffering, retry, backpressure, and replay surfaces are implemented
and currently green under smoke and unit coverage. The reviewer should still treat durable crash
survival and idempotent replay/batch-write semantics as open acceptance questions, not settled
truth.

---

## 3. Live Worktree Evidence Snapshot

### 3.1 Observed telemetry surfaces

| File | Observed role | Why it matters |
|---|---|---|
| `services/telemetry/telemetry_event.schema.json` | Canonical event envelope for binding/stage/governance evidence | Defines the minimum event shape the ingest path is supposed to preserve |
| `services/telemetry/capture.py` | Producer-side binding/stage field injection | Shows how runtimes currently attach canonical references before ingest |
| `services/telemetry/ingest_svc.py` | Ingest facade, evidence validation, buffer enqueue, stats, DLQ replay | Core parent artifact for the telemetry ingest slice |
| `services/telemetry/buffer.py` | In-memory v1 buffer plus Redis Streams v2-ready adapter | Encodes the current durability tradeoff of the shock-absorption path |
| `services/telemetry/batch_writer.py` | Micro-batching, partition routing, retry, poison handling | Encapsulates Layer D behavior from the L1 architecture |
| `services/telemetry/smoke_test_ingest.py` | Runnable acceptance smoke for throughput/backpressure/replay | Gives the parent owner a quick regression command |
| `services/telemetry/test_ingest_shock_absorption.py` | 39-test unit/integration suite for the ingest substrate | Confirms the support packet is backed by executable evidence |
| `services/telemetry/BUFFER_CHOICE_ADR.md` | Buffer durability tradeoff and activation criteria | Explains why the current default is still in-memory and what qualifies as durable activation |

### 3.2 Commands executed for this sidecar

Commands executed on `2026-04-15`:

```bash
python3 services/telemetry/smoke_test_ingest.py
python3 -m unittest services.telemetry.test_ingest_shock_absorption
python3 -m unittest test_capture.TestBindingStageEvidence
```

Observed results:

- `services/telemetry/smoke_test_ingest.py`: `ALL SMOKE TESTS PASSED`
- `services.telemetry.test_ingest_shock_absorption`: `Ran 39 tests in 3.619s` -> `OK`
- `test_capture.TestBindingStageEvidence` from `services/telemetry/`: `Ran 13 tests in 0.069s` -> `OK`

### 3.3 Policy anchors used for review

| Anchor | Relevance |
|---|---|
| `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md` | Canonical L1 definition of the telemetry ingest path, shock absorption, authoritative Postgres role, and replay/backpressure policy |
| `EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md` | Canonical L1 ordering, at-least-once delivery, idempotency, and replay rules |
| `services/telemetry/TEL_001A_FIELD_PACKET.md` | Support packet that spells out ingest-time RuntimeBinding resolution and field-level acceptance checkpoints |
| `services/telemetry/BUFFER_CHOICE_ADR.md` | Operational rationale for the current buffer backend and activation criteria for real durability |

---

## 4. Dependency Map

### 4.1 Upstream dependency already satisfied

| Dependency | Status | Relevance |
|---|---|---|
| `BP5-SVC-007` | done | Telemetry ingest depends on the canonical RuntimeBinding/runtime-manager path being present so events can reference real binding identity and deployment-stage truth |

### 4.2 Direct downstream dependencies

| Task | Depends on BP5-SVC-009 for | Evidence |
|---|---|---|
| `BP5-SVC-010` | lineage reads over normalized telemetry/binding references instead of narrative-only joins | `ai-status.json` lists `BP5-SVC-010` with `depends_on: [BP5-SVC-009]` |
| `BP5-SVC-011` | incident/postmortem services need authoritative telemetry evidence before they can enforce cross-object linkage | `ai-status.json` lists `BP5-SVC-011` with `depends_on: [BP5-SVC-009, BP5-SVC-010]` |

### 4.3 Adjacent consumers that benefit once parent semantics are accepted

| Consumer | Benefit |
|---|---|
| Telemetry lineage read model | can trust `binding_id`, `plan_id`, `persona_capital_binding_id`, and stage labels without heuristic recovery |
| Incident / postmortem evidence flows | can cite canonical telemetry records instead of mirror-only or fallback sources |
| BFF and operator read paths | can eventually consume authoritative telemetry freshness and degradation signals without inventing UI-owned shadow rules |

### 4.4 Policy dependencies the reviewer should keep in view

| Policy source | What to confirm |
|---|---|
| `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md:30-54,82-112,182-250` | the repo state really satisfies the intended Layer B -> C -> D -> E ingest path, not just a test scaffold |
| `EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md:37-42,115-137` | replay and retry semantics remain idempotent under at-least-once delivery |
| `services/telemetry/TEL_001A_FIELD_PACKET.md:134-145,187-220` | RuntimeBindingStore resolution, deployment-stage normalization, temporal checks, and negative tests are either satisfied or explicitly still open |

---

## 5. Review Outcome And Owner Closeout

Reviewer `Claude` approved this helper slice and confirmed:

1. the packet stays within sidecar scope and does not claim the parent telemetry task is already accepted
2. the three highlighted gaps are accurately framed against the current repo state:
   - missing RuntimeBindingStore lookup / temporal-window validation in `ingest_svc.py`
   - default writer still being a memory-only no-op sink
   - missing explicit dedupe or dry-run protection in retry/replay behavior
3. the packet is ready to hand back to the owner for formal closeout and parent-owner use

Owner closeout intent:

- keep this packet as reviewer scaffolding for `BP5-SVC-009`
- leave the three REVIEW callouts attached to the parent task, not this sidecar
- let the parent owner decide whether to absorb the packet directly into the main review flow

---

## 6. Sidecar Scope Declaration

This file is a support artifact only.

- No canonical L1 or L2 document was modified by this sidecar
- No telemetry ingest implementation file was modified by this sidecar
- No runtime-manager, registry, or governance truth was edited by this sidecar
- The only artifact created by this slice is this reviewer packet
