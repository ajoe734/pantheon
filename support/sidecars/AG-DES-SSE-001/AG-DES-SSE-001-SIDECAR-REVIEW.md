# Review Packet: AG-DES-SSE-001 — Typed Workshop SSE Aggregate Contract

**Sidecar Kind:** review_packet  
**Sidecar Task:** AG-DES-SSE-001-SIDECAR-REVIEW  
**Parent Task:** AG-DES-SSE-001  
**Date:** 2026-06-21  
**Prepared by:** Claude  
**Reviewer:** Claude2  
**Status:** Pending review

---

## 1. Task Summary

AG-DES-SSE-001 delivers the **typed, ordered, replayable workshop SSE aggregate-event contract** (design decision 5 of the v1.3 bundle). It replaces the generic untyped SSE stream that existed in v1.1 with a schema-governed event catalog, formal envelope fields, private-content handling rules, replay semantics, and frontend consumption rules.

This is a **design/contract task only**. The schema and prose exist in the design closure package. Implementation into canonical service paths (`services/control-plane/specs/agora/v4/`) is downstream work (AG-BE-SW-004 and the v1.3 bundle merge task).

---

## 2. Artifacts Produced

| Artifact | Location | State |
|---|---|---|
| SSE contract prose (C1–C9) | `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/03_workshop_sse_contract.md` | ✅ Complete |
| `workshop_stream_event.schema.json` (design copy) | `docs/04/…/design-closure-round2/schemas/workshop_stream_event.schema.json` | ✅ Complete |
| OpenAPI v1.3 delta (schema ref) | `docs/04/…/design-closure-round2/08_openapi_v1_3_delta.yaml` — `WorkshopStreamEvent: $ref "../specs/agora/v4/workshop_stream_event.schema.json"` | ✅ Complete |
| Capability manifest v1.3 (SSE capability entry) | `docs/04/…/design-closure-round2/schemas/capability_manifest_v1_3.json` — `agora.workshop.v1 v1.3` includes `v4/workshop_stream_event.schema.json` | ✅ Complete |
| Bundle index template | `docs/04/…/design-closure-round2/bundle_index.v1_3.template.json` — SSE schema hash: `37c21e77…` (template; must be verified on merge) | ✅ Present as template |
| Canonical v4 schema (implementation target) | `services/control-plane/specs/agora/v4/workshop_stream_event.schema.json` | ❌ Not yet created |
| `agora_v1_3.openapi.yaml` (canonical) | `services/control-plane/openapi/agora_v1_3.openapi.yaml` | ❌ Not yet created |
| `bundle_index.v1_3.json` (canonical) | `services/control-plane/specs/agora/bundle_index.v1_3.json` | ❌ Not yet created |

The three "not yet created" items are owned by the v1.3 implementation/merge task — they are **not a gap in this design task**.

---

## 3. Contract Coverage Review

The following table maps each section of `03_workshop_sse_contract.md` to design evidence.

| Section | Requirement | Schema / Spec Evidence | Status |
|---|---|---|---|
| **C1** | Route: `GET /bff/agora/workshops/{workshop_id}/stream` | Route present in `agora_v1_1.openapi.yaml` as `streamAgoraWorkshop`; v1.3 delta binds `WorkshopStreamEvent` schema ref | ✅ |
| **C1** | Content-Type: `text/event-stream` | `agora_v1_1.openapi.yaml` response media type: `text/event-stream` | ✅ |
| **C1** | Auth check before stream opens and on replay | Prose contract only; no schema field (correct — auth is a BFF middleware concern) | ✅ (prose) |
| **C2** | Envelope fields: event_id, event_type, aggregate_type, aggregate_id, sequence_no, causal_parent_id, event_time, emitted_at, trace_id, request_id, idempotency_key, data_cutoff, visibility, payload_schema, payload | All fields present in schema. `visibility`, `payload_schema`, `causal_parent_id`, `request_id`, `data_cutoff` are optional (not in `required`). See §4 for open item. | ✅ (with note) |
| **C2** | `aggregate_type` fixed to `strategy_workshop` | Schema: `"enum": ["strategy_workshop"]` | ✅ |
| **C2** | Per-aggregate ordering, at-least-once delivery, consumer deduplication by `event_id`, sequence gap handling | Prose contract (C2). No schema field can enforce delivery semantics — correct. | ✅ (prose) |
| **C3** | Event catalogue: 23 named event types | Schema `event_type` enum has exactly 23 values matching prose catalogue | ✅ |
| **C4** | p95 command receipt < 2 seconds | Prose SLA only; not a schema property — correct. | ✅ (prose) |
| **C4** | `workshop.message.accepted` references same request_id and persisted event | Prose contract. `request_id` is an optional envelope field. | ✅ (prose) |
| **C5** | `visibility` field with owner-private and redacted-management values | Schema: `"visibility": {"type": "string", "enum": ["owner_private", "owner_and_redacted_management"]}` | ✅ |
| **C5** | Raw text must not appear in event logs or transport diagnostics | Prose contract + L1 private-content policy | ✅ (prose) |
| **C6** | `Last-Event-ID` header for replay | In `agora_v1_1.openapi.yaml` stream route parameters | ✅ |
| **C6** | Replay window: 24 h or 10,000 events | Prose contract | ✅ (prose) |
| **C6** | `SSE_REPLAY_UNAVAILABLE` fallback | Prose contract. Constant is named but not defined as a formal schema enum — see §4. | ✅ (prose) |
| **C7** | `stream.heartbeat` event type | In schema enum | ✅ |
| **C7** | Heartbeat interval 15 s, degraded after 45 s, backoff cap 30 s | Prose contract | ✅ (prose) |
| **C7** | Progress events coalesced ≤ 2/s/run | Prose contract | ✅ (prose) |
| **C8** | `stream.error` event type | In schema enum | ✅ |
| **C8** | Error payload fields: code, message, retryable, operation_ref | Prose contract. No dedicated error payload sub-schema — see §4. | ✅ (prose) |
| **C8** | Error must not echo private content | Prose contract | ✅ (prose) |
| **C9** | Frontend sequence gate, dedup by event_id, gap → snapshot refresh | Prose contract | ✅ (prose) |
| **C9** | React Query/store keys include tenant/user/workshop | Prose contract | ✅ (prose) |
| **C9** | Raw private content not persisted to localStorage | Prose contract | ✅ (prose) |

---

## 4. Open Items for Reviewer

These are design-level questions that should be resolved before AG-BE-SW-004 begins implementation. None block approval of the design package itself, but they should be noted as implementation-side guidance.

### OI-1 — `request_id` is optional in v4 envelope; was required in v3

The v3 schema (`services/control-plane/specs/agora/v3/workshop_event.schema.json`) required `request_id`. The v4 envelope makes it optional. This is arguably correct for system-originated events (heartbeat, snapshot, servant responses) where no inbound request_id exists. However the intent should be documented: events that originate from a BFF command (message.accepted, patch.proposed, etc.) should carry `request_id`; system/push events may omit it.

**Recommendation:** Accept as-is (intentional relaxation). Add implementation note that BFF-originated events MUST populate `request_id`.

### OI-2 — `payload` is untyped object; no per-event-type payload sub-schemas

The envelope `payload` field is `{type: object, additionalProperties: true}`. There are 23 event types but no discriminated-union or `$defs` for each event's payload shape. This matches the design decision to keep the SSE envelope thin and delegate payload contracts to per-event documentation/types. However it means frontend implementations must handle unknown payload shapes at the JSON Schema level.

**Recommendation:** Accept as-is for v1.3. Note that per-event-type payload sub-schemas are a natural v1.4 refinement. AG-BE-SW-004 (implementation) should document the expected payload fields for at least the high-traffic events (message.accepted, servant.response.delta, patch.proposed) in implementation notes.

### OI-3 — `SSE_REPLAY_UNAVAILABLE` error code is prose-only

Section C6 names `SSE_REPLAY_UNAVAILABLE` as the canonical error code returned when replay is unavailable. This constant is not present in the `stream.error` payload sub-schema (which itself is prose-only — see OI-2) or in any schema enum.

**Recommendation:** Accept as-is for the design task. Implementation task should define this in the BFF error code registry.

### OI-4 — `stream.error` payload fields not sub-schematized

C8 specifies `{code, message, retryable, operation_ref}` for the `stream.error` payload. These are described in prose only; no `$defs/StreamError` is present in `workshop_stream_event.schema.json`.

**Recommendation:** Accept as-is. AG-BE-SW-004 should enforce the four fields in the BFF implementation and may optionally add a `$defs` to the schema in a follow-up.

---

## 5. Comparison with v3 Schema

The v4 `workshop_stream_event.schema.json` is an **SSE transport envelope** rather than a DB row schema. Key architectural shifts from v3:

| Dimension | v3 (`workshop_event.schema.json`) | v4 (`workshop_stream_event.schema.json`) |
|---|---|---|
| Purpose | DB row / internal event model | SSE wire format for frontend consumers |
| Event types | 8 (message, version_created, version_selected, research_dispatched, consultation_started, status_changed, concluded, archived) | 23 typed event variants covering full workshop lifecycle + research + consultation + stream metadata |
| Aggregate identity | `workshop_id` (direct field) | `aggregate_type` + `aggregate_id` (generic aggregate envelope) |
| Private content | Inline `private_content_ref`, `redacted_summary`, `redaction_policy_version` fields | `visibility` enum; private payload forwarded to owner; management consumers receive redacted projection |
| Observability | `trace_id`, `request_id` | `trace_id`, `request_id`, `idempotency_key`, `data_cutoff`, `emitted_at`, `payload_schema` |
| Payload sub-typing | Conditional sub-schemas per `event_type` (version_link, conclude_refs, status_change) | Open `payload` object; per-type contract is prose-only |
| `spec_version` | Absent | `"1.0"` (required) |
| Additional properties | Closed (`additionalProperties: false`) | Closed (`additionalProperties: false`) |

The v4 schema is intentionally not a superset of v3 — it targets a different consumption surface. v3 remains the canonical DB row schema; v4 is the SSE transport projection.

---

## 6. Downstream Unblock State

| Task | Condition | State |
|---|---|---|
| `AG-BE-SW-004` | SSE event schema/OpenAPI merged | ❌ Blocked — requires v1.3 bundle merge (canonical v4 path must exist) |
| `AG-FE-SW-002` | CARD + SSE contract available | ❌ Blocked — depends on AG-BE-SW-004 |

The design package is complete. The remaining blocker is the v1.3 bundle implementation task that places schemas in `services/control-plane/specs/agora/v4/` and generates `bundle_index.v1_3.json`.

---

## 7. Evidence Checklist

| Evidence Item | Present | Location |
|---|---|---|
| Design prose (C1–C9) | ✅ | `design-closure-round2/03_workshop_sse_contract.md` |
| Schema JSON draft-07 | ✅ | `design-closure-round2/schemas/workshop_stream_event.schema.json` |
| Event type enum (23 values) matching prose | ✅ | Schema `event_type` enum |
| OpenAPI delta schema reference | ✅ | `08_openapi_v1_3_delta.yaml` components.schemas.WorkshopStreamEvent |
| Capability manifest entry (agora.workshop.v1 v1.3) | ✅ | `capability_manifest_v1_3.json` |
| Bundle template hash (pre-merge placeholder) | ✅ | `bundle_index.v1_3.template.json` key `specs/agora/v4/workshop_stream_event.schema.json` |
| Pre-existing SSE route in v1.1 OpenAPI (`streamAgoraWorkshop`) | ✅ | `agora_v1_1.openapi.yaml` |
| No modification to v1, v1.1, v1.2 bundle files | ✅ | `git diff` confirms no changes to v1.1/v1.2 OpenAPI or bundle files |

---

## 8. Reviewer Handoff Notes for Claude2

**What to review:**

1. Confirm C1–C9 prose coverage is complete and internally consistent.
2. Confirm the 23-event enum matches the full event lifecycle you would expect (nothing missing, nothing spurious).
3. Review the four open items (§4) and record whether each is accepted as-is or requires a schema amendment before merge.
4. Verify the v4 schema `additionalProperties: false` does not accidentally block valid future fields. (Current design is intentionally strict — confirm this is the intended posture for v1.3.)
5. Confirm the `visibility` enum covers the needed access tiers. (v1.3 has two values; a third tier may be needed for institutional-read-only consumers in the future.)

**What NOT to review in this sidecar:**
- The canonical `services/control-plane/specs/agora/v4/` path does not exist yet — do not treat this as a gap in the design task.
- Other v1.3 schemas (trading room, research, etc.) are not in scope for this review.

**Approval outcome:**
- **APPROVED** → owner (Claude) can proceed to mark AG-DES-SSE-001 done and hand off to AG-BE-SW-004 implementation.
- **APPROVED with corrections** → list specific corrections; owner updates design doc and/or schema before proceeding.
- **BLOCKED** → describe what additional design work is needed.

---

*Review packet prepared by Claude on 2026-06-21.*  
*Parent task owner: Claude. Reviewer: Claude2.*
