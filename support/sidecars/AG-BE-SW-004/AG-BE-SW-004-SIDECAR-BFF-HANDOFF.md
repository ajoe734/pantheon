# AG-BE-SW-004 Sidecar BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Sidecar task | `AG-BE-SW-004-SIDECAR-BFF-HANDOFF` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-SW-004` - Streaming workshop aggregate |
| Parent owner / reviewer | `Codex2` / `Codex` |
| Sidecar owner / reviewer | `Codex` / `Codex2` |
| Date | 2026-06-21 |
| Mutates canonical truth | `false` |
| Status | Ready for Codex2 review |

This is a support-only packet. It does not modify L1 canonical truth, core
contract truth, BFF routes, OpenAPI/schema files, runtime, registry,
governance, or frontend implementation. It records the current BFF query gap,
safe operator journey, and frontend handoff boundary for the parent owner and
reviewer to decide whether `AG-BE-SW-004` waits for design closure or narrows
scope.

## Purpose

`AG-BE-SW-004` asks for `/bff/agora/workshops/{id}/stream` to aggregate
message ack, completeness updates, research progress, and version events over
SSE, with first acknowledgement under 2 seconds, reconnect support, trace/audit
fields, and `OPENCLAW_UPSTREAM_DEGRADED` downgrade behavior.

Current repo truth does not yet define that typed aggregate stream contract.
This packet separates what exists today from what must be specified before
runtime or frontend work can safely proceed.

## Sources Read

| Source | Purpose |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | Collaboration rules and canonical layer boundaries. |
| `.orchestrator/task-briefs/ag_be_sw_004_sidecar_bff_handoff.md` | Sidecar scope and support-only artifact path. |
| `.orchestrator/skills/worker-anchor-commit.md` | Commit and scope discipline. |
| `.orchestrator/skills/task-closeout-finalization.md` | Review/closeout expectations. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-004` | Parent status is `blocked`, waiting for Codex clarification/review. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-004-SIDECAR-BFF-HANDOFF` | Sidecar status is `in_progress`, owner `Codex`, reviewer `Codex2`. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/OPEN_DESIGN_GAPS_ROUND2_FOR_SD_TEAM_2026-06-21.md` | Explicitly identifies missing workshop SSE aggregate event schema as Gap C. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/SD_2026-06-20.md` | v1 route/catalog anchor; no typed workshop stream payload. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/03_servant_and_workshop_contracts.md` | v1.1 workshop route family and route ownership. |
| `services/control-plane/openapi/agora_v1_1.openapi.yaml` and `agora_v1_2.openapi.yaml` | `/stream` is `text/event-stream` string only; no aggregate event schema. |
| `services/control-plane/specs/agora/v3/workshop_event.schema.json` | Closest current event-row schema. |
| `services/control-plane/specs/agora/v3/workshop_persistence.schema.json` | Persistence rows and indexes for workshop events/completeness. |
| `services/control-plane/specs/agora/strategy_completeness.schema.json` | Public completeness assessment shape. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | Current runtime: list/create/get/message/events/completeness live; stream and several mutation routes are 501 stubs. |
| `services/control-plane/bff/tests/test_agora_strategy_workshop.py` | Focused evidence for current workshop router behavior. |
| `services/control-plane/bff/models.py` and `services/control-plane/bff/agora/models.py` | `OPENCLAW_UPSTREAM_DEGRADED` is not a canonical BFF or Agora error enum. |
| `services/control-plane/bff/agora/servant/router.py` | `OPENCLAW_UPSTREAM_DEGRADED` exists as servant provider payload marker only. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/05_execute_plans_agora_ui_ia_and_dependencies.md` | Strategy Workshop IA and frontend BFF boundary. |
| `support/sidecars/AG-DES-SW-DB-001/AG-DES-SW-DB-001-SIDECAR-BFF-HANDOFF.md` | Prior workshop BFF/frontend handoff baseline; treated as historical support context. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## Executive Conclusion

`AG-BE-SW-004` should remain blocked unless the parent scope is narrowed.

There are two coherent paths:

| Path | Meaning | Consequence |
|---|---|---|
| Wait for design closure | A new additive contract defines typed aggregate stream events for message ack, completeness update, research progress, and version activity. | Parent can implement the requested task without inventing event names or payloads. |
| Narrow scope | Parent implements only an SSE replay of existing `strategy_workshop_event` rows, equivalent to incremental `GET /events`. | This is not the originally requested aggregate stream; completeness/research/version progress remains polling or later follow-up. |

The current repo does not support a truthful implementation of the full parent
request. The sidecar therefore hands off blocker evidence and frontend-safe
fallback guidance, not a runtime patch.

## As-Found BFF State

Current implemented workshop routes in
`services/control-plane/bff/agora/strategy_workshop/router.py`:

| Route | Runtime state | Notes |
|---|---|---|
| `GET /bff/agora/workshops` | Implemented | User/tenant scoped list, status filter, cursor/limit. |
| `POST /bff/agora/workshops` | Implemented | Requires `Idempotency-Key`; creates session and initial `message` event. |
| `GET /bff/agora/workshops/{id}` | Implemented | Returns ETag header `W/"workshop:{id}:vN"`. |
| `POST /bff/agora/workshops/{id}/messages` | Implemented | Requires `If-Match` and `Idempotency-Key`; CAS appends event and increments lock version. |
| `GET /bff/agora/workshops/{id}/events` | Implemented | Ordered event rows; supports `after_sequence`. |
| `GET /bff/agora/workshops/{id}/completeness` | Implemented | Returns latest snapshot row or `null`. |
| `GET /bff/agora/workshops/{id}/versions` | 501 stub | Not implemented. |
| `POST /bff/agora/workshops/{id}/versions` | 501 stub | Not implemented. |
| `POST /bff/agora/workshops/{id}/versions/{vid}/select` | 501 stub | Not implemented. |
| `POST /bff/agora/workshops/{id}/research-runs` | 501 stub | Not implemented. |
| `POST /bff/agora/workshops/{id}/consultations` | 501 stub | Not implemented. |
| `POST /bff/agora/workshops/{id}/conclude` | 501 stub | Not implemented. |
| `GET /bff/agora/workshops/{id}/stream` | 501 stub | Parent target route, not implemented. |

Focused validation confirms this current state: `54 passed` in
`tests/test_agora_strategy_workshop.py`.

## BFF Query Gap Ledger

| Gap | Current evidence | Frontend/BFF handoff rule |
|---|---|---|
| Typed aggregate stream schema missing | Round 2 design gap doc names Gap C; v1.1/v1.2 OpenAPI defines `/stream` as `text/event-stream` string only. | Do not emit or consume invented event names such as `message_ack`, `completeness_update`, or `research_progress` until an additive schema lands. |
| Stream route not live | Router registers `/stream` but returns 501 `NOT_IMPLEMENTED`. | Frontend must treat stream as unavailable and use polling against `/events` and `/completeness` if it needs current safe behavior. |
| Ack semantics missing | `POST /messages` returns `202` with event id/sequence, but no SSE ack event contract or first-ack timer definition exists. | Do not claim `<2s` stream acknowledgement until the event contract defines ack payload and measurement boundary. |
| Completeness update event missing | Completeness is a latest snapshot query. The v3 event schema has no `completeness_updated` event type. | Poll `GET /completeness`; do not infer stream events from snapshot writes. |
| Research progress source missing | `POST /research-runs` is 501 and Round 2 Gap B says research run projection/progress SSE is unspecified. | Do not render research progress from workshop stream. Any progress card remains gated on research facade design. |
| Version event source only partly specified | v3 `workshop_event` includes `version_created` and `version_selected`, but BFF version routes/store APIs are 501/missing in this router. | Version UI can be designed around future refs, but runtime stream cannot publish version events from this route family yet. |
| Degraded code boundary unclear | `OPENCLAW_UPSTREAM_DEGRADED` exists as a servant provider payload marker. It is not in `ErrorCode`, `AgoraErrorCode`, or workshop OpenAPI error response enums. | For workshop stream, do not use that token as a canonical error code unless the parent/spec promotes it. Use existing BFF error envelopes for route failures. |
| Trace/audit fields not consistently available | v3 `workshop_event` requires `trace_id` and `request_id`; current runtime `MemoryWorkshopStore` and router allow them to be `None` for created message events. | Stream cannot truthfully promise trace/audit on every event without tightening write paths or defining an audit projection. |

## Existing Event-Row Baseline

The closest usable payload today is the v3 `WorkshopEvent` row schema:

```text
event_id
workshop_id
sequence_no
actor_type = user | servant_persona | system
actor_ref
event_type = message | version_created | version_selected |
             research_dispatched | consultation_started |
             status_changed | concluded | archived
private_content_ref
redacted_summary
redaction_policy_version
version_link
conclude_refs
status_change
payload_refs
trace_id
request_id
created_at
```

This is enough for an event-row stream if the parent is narrowed to
"SSE equivalent of ordered `/events` replay." It is not enough for the current
aggregate stream request because it does not define separate ack/completeness
progress payloads or their ordering rules.

## Safe Operator Journey

Until the stream contract is closed, the frontend-safe journey is polling based:

1. List active workshops with `GET /bff/agora/workshops?status=open` or the
   supported status filters already implemented by the BFF.
2. Create a workshop with `POST /bff/agora/workshops`, including
   `Idempotency-Key`.
3. Enter detail with `GET /bff/agora/workshops/{id}` and store the returned
   ETag header.
4. Append an operator message with `POST /bff/agora/workshops/{id}/messages`,
   including latest `If-Match` plus `Idempotency-Key`.
5. Read new events through
   `GET /bff/agora/workshops/{id}/events?after_sequence={last_seen}`.
6. Read completeness through `GET /bff/agora/workshops/{id}/completeness`
   after message append, timer tick, or visible refresh.
7. Treat versions, research-runs, consultations, conclude, and stream as not
   available until their routes stop returning 501 and their field contracts are
   reviewed.

Frontend must not hide 501/503 route failure as an empty-success state.

## Frontend Handoff Notes

For execute-plans Strategy Workshop work:

| Surface | Current handoff |
|---|---|
| Conversation timeline | Bind to `/events` with `after_sequence`; render server event rows only. |
| Composer acknowledgement | Use the `202` response from `POST /messages` as the only current ack. Do not wait for a stream ack. |
| Completeness rail | Bind to `/completeness`; refresh after message submission or manual refresh. |
| Research cards | Keep gated or placeholder-fail-closed; no workshop research progress stream exists. |
| Version compare/card | Keep gated on version route implementation; do not synthesize version events. |
| Stream subscription | Feature-detect `/stream`; if it returns 501/404/error, fall back to polling without changing displayed truth. |
| Error handling | Show BFF error envelopes directly enough for operator action; do not map provider degraded payloads into canonical workshop errors unless specified. |
| BFF client boundary | Use the existing `src/lib/bff-v1/agora/*` client layer; no direct `fetch()` in pages. |

## Unblock Questions For Parent/SD

These are contract questions, not sidecar implementation tasks:

1. Is `AG-BE-SW-004` allowed to narrow to an event-row SSE stream only, or must
   it wait for typed aggregate event design?
2. If aggregate stream is required, what is the canonical event envelope:
   event id, event type enum, sequence cursor, retry/reconnect metadata, audit,
   trace, and data shape?
3. What exact event type names should represent:
   message acknowledgement, completeness update, research progress, version
   created/selected, degraded provider, and terminal/error?
4. What is the authoritative source for each aggregate event:
   `strategy_workshop_event`, `strategy_completeness_snapshot`, research facade,
   Strategy Registry, OpenClaw adapter, or composed BFF projection?
5. How is the `<2s` first acknowledgement measured: HTTP `202`, first SSE event,
   provider enqueue event, or persisted workshop event?
6. Is `OPENCLAW_UPSTREAM_DEGRADED` promoted to a workshop stream payload marker,
   a canonical BFF error code, or kept servant-only?
7. What is the reconnect contract for `Last-Event-ID`: sequence number, event id,
   bounded replay window, or forced resync route?

## Future Implementation Acceptance If Unblocked

The parent implementation should add focused tests for:

| Test area | Required assertion |
|---|---|
| Stream route | `GET /stream` returns `text/event-stream` and no longer returns 501. |
| Authorization | 401/403/404 behavior matches existing workshop ownership checks. |
| Replay | `Last-Event-ID` or sequence cursor resumes without duplication. |
| Privacy | Raw private content never appears in SSE replay or audit fields. |
| Ack timing | First accepted-message acknowledgement is emitted under the contract-defined `<2s` boundary. |
| Completeness | Completeness events are emitted only from a specified source and match schema. |
| Research progress | Progress events are emitted only when the research facade/run source is live. |
| Version events | Version-created/selected events are emitted only after version routes/store are live. |
| Degraded behavior | Provider/upstream degradation uses the promoted contract field or canonical BFF envelope. |
| Backpressure | Stream replay/buffering follows existing BFF SSE bounded-buffer expectations. |

## Validation Run

Commands run from this task worktree:

```bash
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-004
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-004-SIDECAR-BFF-HANDOFF
python3 -m json.tool services/control-plane/specs/agora/v3/workshop_event.schema.json
python3 -m json.tool services/control-plane/specs/agora/v3/workshop_persistence.schema.json
python3 -m json.tool services/control-plane/specs/agora/strategy_completeness.schema.json
(cd services/control-plane/bff && python3 -m pytest tests/test_agora_strategy_workshop.py -q)
```

Observed results:

| Command | Result |
|---|---|
| `ai-status.sh show AG-BE-SW-004` | PASS; parent is `blocked`, waiting for Codex, blocker text matches missing aggregate stream contract. |
| `ai-status.sh show AG-BE-SW-004-SIDECAR-BFF-HANDOFF` | PASS; sidecar is `in_progress`, owner `Codex`, reviewer `Codex2`, artifact path matches this file. |
| JSON schema parses | PASS for `workshop_event`, `workshop_persistence`, and `strategy_completeness`. |
| `pytest tests/test_agora_strategy_workshop.py -q` | PASS; `54 passed in 80.04s`. |

## Non-Scope

This sidecar intentionally does not:

- implement `/bff/agora/workshops/{id}/stream`;
- edit OpenAPI, JSON schemas, BFF router/store, error enums, or tests;
- promote `OPENCLAW_UPSTREAM_DEGRADED` into a canonical workshop error;
- update execute-plans frontend code;
- unblock the parent task by inventing stream fields.

## Reviewer Handoff

Recommended reviewer approval command:

```bash
AI_NAME=Codex2 REVIEW_FILE=support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF.md \
  REVIEW_NOTES_ZH="Support-only handoff accurately captures the AG-BE-SW-004 workshop stream blocker, current BFF route state, frontend polling fallback, and required contract decisions without changing canonical truth or runtime files." \
  ./scripts/ai-status.sh approve AG-BE-SW-004-SIDECAR-BFF-HANDOFF \
  "Sidecar handoff approved; parent owner can decide whether to keep AG-BE-SW-004 blocked for typed aggregate stream design or narrow to event-row SSE."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh reopen AG-BE-SW-004-SIDECAR-BFF-HANDOFF \
  "Describe the factual correction needed in the handoff packet."
```

*Prepared by Codex for `AG-BE-SW-004-SIDECAR-BFF-HANDOFF`.*
