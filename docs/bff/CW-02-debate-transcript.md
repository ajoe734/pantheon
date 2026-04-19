# CW-02 Debate Transcript BFF Contract

## Status

**Contract published** — the `TranscriptEvent` schema, actor labeling contract, inline evidence behavior, and transcript route shape are the definitive implementation target for the Pantheon BFF. UI work must not start until Pantheon confirms the route is live and returning this field shape.

Task: `CW-02-TRANSCRIPT-001`

## Purpose

Provide one ordered, BFF-resolved transcript surface for the Consultation Workbench so operators can replay a debate's full event sequence, see actor-labeled contributions, and tap inline evidence links — without the browser resolving actor identity, constructing evidence URLs, or inferring event ordering from raw participant traffic.

## Dependency

Requires stable `ConsultRequest` identity and `linked_session_id` from `CW-01-FOUNDATION-001`.

## Routes

### Get transcript

- `GET /api/v1/consultations/{session_id}/transcript`

Supported query params:

- `page_token`
- `page_size`
- `from_sequence` — optional; return only events with `sequence_number >= from_sequence` (for incremental load / replay scrubbing)

Required response fields:

- `session_id`
- `linked_request_id` — from the originating `ConsultRequest`
- `events[]` — ordered ascending by `sequence_number`; see `TranscriptEvent Object` below
- `last_event_at` — timestamp of the highest-sequence event returned; used for degraded partial-transcript banner
- `page_info.next_page_token`
- `page_info.total`
- `meta.snapshot_at`
- `meta.surfaces.transcript` — `"ok"` | `"partial"` | `"degraded"` | `"unavailable"`

## TranscriptEvent Object

Each event in `events[]` must contain:

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `event_id` | string | no | canonical event identity |
| `session_id` | string | no | parent consultation session |
| `sequence_number` | integer | no | strictly monotonically increasing; guarantees append-only ordering |
| `actor_id` | string | no | raw participant identity (persona ID or system actor) |
| `actor_label` | string | no | BFF-resolved display name; client must not compute this from raw refs |
| `actor_role` | string | no | `"requester"` \| `"responder"` \| `"committee_participant"` \| `"sponsor"` \| `"system"` |
| `actor_role_badge` | string | no | BFF-resolved role badge label for UI rendering |
| `event_type` | string | no | `"message"` \| `"evidence_attachment"` \| `"outcome_signal"` \| `"escalation_signal"` |
| `body` | string | yes | event text; present for `message`, `outcome_signal`, `escalation_signal`; null for `evidence_attachment` |
| `evidence_ref` | string | yes | raw canonical evidence identifier; present only when `event_type = "evidence_attachment"` |
| `evidence_link` | object | yes | BFF-resolved evidence navigation target; present only when `event_type = "evidence_attachment"` — see Evidence Link Object |
| `emitted_at` | ISO 8601 string | no | wall-clock time the event was recorded |

### Evidence Link Object

When `event_type = "evidence_attachment"` the BFF must pre-resolve `evidence_ref` into `evidence_link`:

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `evidence_link.href` | string | no | BFF-constructed URL or route path to the canonical evidence surface |
| `evidence_link.label` | string | no | short human-readable label for the tappable link (e.g. `"Telemetry spike — 2026-04-19"`) |
| `evidence_link.surface_type` | string | no | `"telemetry"` \| `"lineage"` \| `"incident"` \| `"deployment_plan"` \| `"note"` |

The client must never construct `evidence_link.href` from raw `evidence_ref`. If the BFF cannot resolve the ref at serve time, it must return `evidence_link = null` and the client renders a non-tappable placeholder.

## Actor Labeling Contract

The BFF must resolve actor identity against the participant roster for the session before serving the transcript:

1. Look up each unique `actor_id` in the `SessionPersona` participant list for `session_id`.
2. Assign `actor_label` from the resolved participant display name.
3. Assign `actor_role` from the participant's `consultation.role` field in `SessionPersona.metadata.consultation`.
4. Assign `actor_role_badge` — a short BFF-owned label string (e.g. `"Requester"`, `"Committee"`, `"Sponsor"`, `"System"`).

If a participant cannot be resolved (session data missing or degraded), the BFF returns:

- `actor_label = "Unknown Actor"`
- `actor_role = "system"`
- `actor_role_badge = "Unknown"`

The client must never call a separate persona-lookup endpoint to fill in missing actor labels.

## Append-Only Ordering Guarantee

- `sequence_number` is the authoritative event ordering source.
- `sequence_number` values are strictly increasing within a session (no gaps required, but no reuse allowed).
- The BFF must return `events[]` sorted ascending by `sequence_number` regardless of `emitted_at` skew.
- Replay mode must use `sequence_number` order, not `emitted_at` order.
- The BFF must never reorder, delete, or re-emit events with duplicate `sequence_number` values.

## Degradation Rules

| `meta.surfaces.transcript` value | UI behavior |
|---|---|
| `"ok"` | render full transcript without banners |
| `"partial"` | render available events plus a non-dismissable partial-transcript banner; include `last_event_at` in banner copy |
| `"degraded"` | render available events with a non-dismissable degraded banner; note that the transcript may be incomplete and staleness may exist |
| `"unavailable"` | suppress the transcript list entirely; show only the canonical unavailable banner; do not render any event rows |

When `meta.surfaces.transcript` is not `"ok"`, the UI must never present the partial or degraded transcript as if it were complete.

## Non-Goals

- The client must not resolve `actor_id` to a display label from raw participant refs.
- The client must not construct `evidence_link.href` from raw `evidence_ref`.
- The client must not re-sort events by `emitted_at` for display or replay.
- The client must not infer session participant roles from the raw event stream.
- The BFF must not maintain its own transcript state machine separate from the Persona Plane's event log.

## Relationship to Existing Consultation Surfaces

- `GET /api/v1/consultations/{session_id}` (CS-02 from `CONSULTATION_SURFACE_CONTRACT.md`) provides the consultation session detail. The transcript route is additive — it provides ordered event-level granularity that CS-02 does not expose.
- `GET /api/v1/consultations/{session_id}/participants` (CS-03) provides the raw participant roster. The transcript route consumes that internally for actor labeling — the client does not need to call CS-03 separately to render actor badges.
- Evidence refs in transcript events point to canonical surfaces already defined by CS-05 (evidence surface). The `evidence_link.href` follows CS-05 routing conventions.

## Write Authority

This packet defines no write routes. Transcript events are produced exclusively by the Persona Plane as consultation sessions progress. The BFF exposes a read-only ordered projection.

## Example Payload

- `docs/examples/CW-02-debate-transcript.json`
