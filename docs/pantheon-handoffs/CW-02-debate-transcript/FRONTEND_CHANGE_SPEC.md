# CW-02 Debate Transcript — Frontend Change Spec

## Feature

- Feature ID: `CW-02-debate-transcript`
- Screen ID: `screen-consultation-debate-transcript`
- Workbench: Consultation Workbench
- Packet status: route-live — UI implementation may proceed against the live transcript route
- Task: `CW-02-TRANSCRIPT-001`

## Readiness Gate

Pantheon has confirmed the following route is live and returning the published field shape:

1. `GET /api/v1/consultations/{session_id}/transcript` — returns the ordered append-only `TranscriptEvent` stream, pagination, optional `from_sequence_no` replay filtering, and `meta.surfaces.transcript.state`.

Build the production transcript surface against this live route. If any required field is absent or diverges from the synced contract, emit `.coordination/requests/CW-02-debate-transcript-bff-gap.yaml` instead of inventing actor identity, transcript ordering, or evidence-link navigation locally.

## Summary

Build the **Debate Transcript** surface inside `front-ai-trading-system`. This slice renders the ordered consultation transcript timeline, actor badges, event content, and inline evidence references for an existing consultation session. All event ordering, actor identity, redaction semantics, and transcript health come from the Pantheon BFF. The frontend must not turn this surface into a chat-style local state machine.

## Files to Create or Modify

```text
src/pages/consultation/DebateTranscript.tsx         — new transcript page
src/pages/consultation/ConsultationTranscriptTypes.ts — transcript response and event types
src/lib/bffClient.ts                                — add CW-02 transcript fetch call
```

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` calls in component files.

### Get consultation transcript

```http
GET /api/v1/consultations/{session_id}/transcript
```

Query parameters (all optional):

| Param | Type | Notes |
|---|---|---|
| `page_token` | string | Opaque pagination cursor |
| `page_size` | number | Default 50 |
| `from_sequence_no` | number | Return only events where `sequence_no >= from_sequence_no` |

Expected response shape (see `docs/examples/CW-02-debate-transcript.json` for full examples):

```typescript
interface TranscriptResponse {
  object_ref: {
    type: "ConsultTranscript";
    id: string;
  };
  transcript_id: string;
  session_id: string;
  linked_request_id: string;
  events: TranscriptEvent[];
  page_info: {
    next_page_token: string | null;
    page_size: number;
    total?: number;
  };
  meta: {
    snapshot_at: string;
    staleness: {
      status: string;
      as_of: string;
      max_age_seconds?: number;
      served_from?: string;
    };
    surfaces: {
      transcript: {
        state: "ok" | "partial" | "degraded" | "unavailable";
      };
    };
  };
}

interface TranscriptEvent {
  transcript_id: string;
  session_id: string;
  event_id: string;
  sequence_no: number;
  parent_event_id: string | null;
  event_type: "message" | "evidence_attachment" | "outcome_signal" | "escalation_signal";
  event_time: string;
  ingest_time: string;
  actor: {
    actor_type: string;
    actor_id: string;
    display_name: string | null;
    role: string;
  };
  content: {
    format: "markdown" | "plaintext";
    text: string | null;
  };
  evidence_refs: string[];
  visibility: string;
  redaction: {
    is_redacted: boolean;
    reason: string | null;
  };
  meta: {
    source: string | null;
    hash: string | null;
  };
}
```

## Component Structure

### `DebateTranscript.tsx`

- Route: `/consultation/transcripts/:session_id`
- Fetches `GET /api/v1/consultations/{session_id}/transcript` on mount.
- Preserve backend ordering exactly as returned. Do not re-sort rows by `event_time`; replay order is `sequence_no`.
- Render `actor.display_name` when present. When it is null, fall back to `actor.actor_id` as raw identity text. Do not invent a display label from roster position or role alone.
- Render `content.text` as markdown only when `content.format === "markdown"`. `plaintext` stays plain text.
- When `redaction.is_redacted === true`, show a redaction indicator and the backend-provided `redaction.reason`. Do not reveal or reconstruct hidden content.
- `evidence_refs[]` are canonical reference ids. Render them as backend-owned reference chips or hand them to a shared evidence-ref surface that resolves through the KW-03 contract. Do not construct evidence URLs from raw `ref_id` values inside CW-02.
- Support pagination through `next_page_token`. If the UI offers replay jumping, wire it to `from_sequence_no`; do not slice the current client-side array and pretend it is authoritative.
- Show `meta.staleness` as the freshness source of truth. Do not derive freshness from the newest `event_time`.

## Degradation Handling

| `meta.surfaces.transcript.state` | Required behavior |
|---|---|
| `ok` | Normal transcript display |
| `partial` | Show a non-dismissable partial-data banner and keep the ordered transcript visible |
| `degraded` | Show a non-dismissable degraded banner and keep the last-known transcript visible; do not present it as complete |
| `unavailable` | Suppress transcript rows entirely and show the canonical unavailable notice |

## Constraints

- Use the existing BFF client only. Do not add raw network calls in component files.
- Do not build a local transcript cache, optimistic chat history, or inferred actor roster.
- Do not infer canonical actor identity from `display_name`, role order, or local participant metadata.
- Do not repair missing `sequence_no` gaps client-side.
- Do not construct evidence links from raw `evidence_refs[]`.
- Do not treat `partial` as permission to ignore transcript integrity or ordering rules.

## References

- BFF contract: `docs/bff/CW-02-debate-transcript.md`
- Example payload: `docs/examples/CW-02-debate-transcript.json`
- Packet family: `docs/pantheon-handoffs/CW-008-consultation-workbench/PACKET_FAMILY.md`
