# C — Typed Strategy Workshop SSE Aggregate Contract

## C1. Route

```text
GET /bff/agora/workshops/{workshop_id}/stream
```

Content type:

```text
text/event-stream
```

Authorization is checked before the stream opens and on replay. A user cannot use a guessed workshop ID to infer another user's stream.

## C2. Envelope

Every event follows `workshop_stream_event.schema.json` and includes:

```text
event_id
event_type
aggregate_type = strategy_workshop
aggregate_id = workshop_id
sequence_no
causal_parent_id
event_time
emitted_at
trace_id
request_id
idempotency_key
data_cutoff
visibility
payload_schema
payload
```

The contract follows per-aggregate ordering and at-least-once delivery. Consumers deduplicate by `event_id` and reject/regap out-of-order sequence numbers.

## C3. Event catalogue

```text
workshop.snapshot
workshop.message.accepted
workshop.servant.response.started
workshop.servant.response.delta
workshop.servant.response.completed
workshop.completeness.updated
workshop.next_question.updated
workshop.patch.proposed
workshop.patch.validated
workshop.version.created
workshop.version.selected
workshop.readiness.updated
research.plan.created
research.plan.approved
research.plan.cancelled
research.run.queued
research.run.progress
research.run.completed
research.run.failed
consultation.started
consultation.completed
workshop.concluded
workshop.archived
stream.heartbeat
stream.error
```

## C4. Message latency contract

For a workshop message:

1. HTTP command validates, redacts/encrypts, persists the message event and returns a command receipt.
2. p95 command receipt target: **< 2 seconds**.
3. `workshop.message.accepted` must reference the same request ID and persisted event.
4. The servant may continue asynchronously.
5. Long work is represented by started/progress/completed events; the client is never expected to keep the POST request open.

The <2 second target applies to the persisted acknowledgement, not completion of LLM/research work.

## C5. Private content

- Owner-private servant response deltas may be streamed to the owner.
- Persisted replay resolves owner content through the private-content contract.
- Management/institutional consumers receive only redacted projections.
- Raw text must not appear in event logs, transport diagnostics or cross-user replay stores.

## C6. Replay

- Client sends `Last-Event-ID`.
- Server replays by workshop sequence.
- Minimum replay window: last 24 hours or last 10,000 events for that workshop, whichever limit is reached first.
- If unavailable, return canonical `SSE_REPLAY_UNAVAILABLE`; the client fetches the latest workshop snapshot and reconnects.
- Replay is side-effect free.

## C7. Heartbeat and connection behavior

- Heartbeat interval: 15 seconds.
- Client declares degraded after 45 seconds without event/heartbeat.
- Reconnect uses exponential backoff capped at 30 seconds.
- Terminal events are never dropped/coalesced.
- Progress events may be coalesced to at most 2 events/second/run.
- The stream must not rely on global event ordering.

## C8. Error payload

`stream.error` contains:

```text
code
message
retryable
operation_ref
```

It must not echo private content.

## C9. Frontend rules

- Apply events only when sequence is the next expected sequence.
- Duplicate `event_id` is ignored.
- A gap triggers a snapshot refresh.
- React Query/store keys include tenant/user/workshop.
- Raw private message content is not persisted to localStorage/sessionStorage.
