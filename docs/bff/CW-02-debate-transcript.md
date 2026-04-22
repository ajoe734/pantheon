# CW-02 Debate Transcript BFF Contract

## Status

**Contract ratified — pending BFF implementation.** The `2026-04-22`
follow-up architecture response closes the remaining transcript contract
questions. `CW-02` is no longer blocked on system design; the remaining gap is
implementing the route family against the ratified append-only event model.

Task: `CW-02-TRANSCRIPT-001`

## Purpose

Provide one ordered transcript surface for the Consultation Workbench so
operators can replay the committee event sequence, inspect actor-authored
messages, and follow evidence references without the client inventing actor
identity, event ordering, or transcript integrity semantics.

## Dependencies

- `CW-01-FOUNDATION-001` for stable `ConsultRequest` identity and linked
  session identity

## Routes

### Get transcript

- `GET /api/v1/consultations/{session_id}/transcript`

Supported query params:

- `page_token`
- `page_size`
- `from_sequence_no` — optional; return only events with
  `sequence_no >= from_sequence_no`

Required response fields:

- `object_ref`
  - `type = "ConsultTranscript"`
  - `id = transcript_id`
- `transcript_id`
- `session_id`
- `linked_request_id`
- `events[]` — ordered ascending by `sequence_no`
- `page_info.next_page_token`
- `page_info.page_size`
- `page_info.total` — optional
- `meta.snapshot_at`
- `meta.staleness`
- `meta.surfaces.transcript.state` — `ok | partial | degraded | unavailable`

## TranscriptEvent Object

Each event in `events[]` must contain:

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `transcript_id` | string | no | parent transcript identity |
| `session_id` | string | no | parent consultation session |
| `event_id` | string | no | canonical event identity |
| `sequence_no` | integer | no | append-only ordering key |
| `parent_event_id` | string | yes | parent event identity when reply / derivation is modeled |
| `event_type` | string | no | `message \| evidence_attachment \| outcome_signal \| escalation_signal` |
| `event_time` | ISO 8601 string | no | canonical domain event time |
| `ingest_time` | ISO 8601 string | no | transcript ingest time |
| `actor.actor_type` | string | no | canonical actor type from the transcript owner |
| `actor.actor_id` | string | no | canonical actor identity from the transcript owner |
| `actor.display_name` | string | yes | optional BFF-enriched label; not canonical identity |
| `actor.role` | string | no | canonical role label carried by upstream transcript truth |
| `content.format` | string | no | `markdown \| plaintext` |
| `content.text` | string | yes | present for textual events |
| `evidence_refs[]` | string[] | no | canonical evidence identifiers attached to the event |
| `visibility` | string | no | transcript visibility scope |
| `redaction.is_redacted` | boolean | no | redaction flag |
| `redaction.reason` | string | yes | redaction reason when applicable |
| `meta.source` | string | yes | upstream transcript producer |
| `meta.hash` | string | yes | integrity / dedupe metadata when available |

## Canonical actor rule

- `actor.actor_type` and `actor.actor_id` are canonical transcript identity
  fields and must originate from the consultation / transcript owner.
- BFF may enrich `actor.display_name`, but must not invent actor identity.
- Frontend must never infer canonical actor identity from roster order, local
  labels, or role heuristics.

## Append-only ordering rule

- `sequence_no` is the authoritative ordering source.
- `sequence_no` values are strictly increasing within a transcript.
- Replay mode must use `sequence_no` order, not `event_time` order.
- BFF must not reorder, delete, or duplicate events in the transcript stream.

## Partial transcript semantics

`partial` is allowed only when transcript enrichment is incomplete but the
append-only event stream remains trustworthy.

Allowed `partial` cases:

- actor display label not yet resolved
- evidence-link enrichment not yet resolved
- attachment display metadata not yet available

Not allowed for `partial`:

- sequence gap
- untrusted ordering
- event loss
- transcript integrity failure

If the event stream itself is inconsistent, use `degraded`, not `partial`.

## Degradation rules

| `meta.surfaces.transcript.state` | UI behavior |
|---|---|
| `ok` | render transcript normally |
| `partial` | render transcript plus non-dismissable partial-data banner |
| `degraded` | render last-known transcript with non-dismissable degraded banner; do not present it as complete |
| `unavailable` | suppress transcript body and show canonical unavailable banner |

Freshness must be represented through `meta.staleness`, not through a primary
surface state of `stale`.

## Non-goals

- The client must not infer actor identity from participant-roster order.
- The client must not repair missing `sequence_no` gaps locally.
- The client must not treat `partial` as permission to ignore transcript
  integrity rules.
- The BFF must not become the canonical writer of transcript identity or event
  ordering.

## Example Payload

- `docs/examples/CW-02-debate-transcript.json`
