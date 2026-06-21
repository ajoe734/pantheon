# AG-BE-SW-004 Sidecar Follow-up 2: BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Task ID | `AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-SW-004` - Streaming workshop aggregate |
| Parent owner / reviewer | `Codex2` / `Codex` |
| Prepared by | `Codex` |
| Reviewer | `Codex2` |
| Date | 2026-06-21 |
| Mutates canonical truth | `false` |
| Status | Ready for Codex2 review |

This is a support-only follow-up packet. It does not modify L1 canonical truth,
core contract truth, OpenAPI/schema files, BFF runtime, registry, governance, or
execute-plans frontend code. It supplements the already merged packet
`support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF.md` by turning
the blocker evidence into a parent absorption checklist and a frontend
fail-closed handoff matrix.

## Relationship To Prior Packet

The prior sidecar packet for `AG-BE-SW-004-SIDECAR-BFF-HANDOFF` was merged in
PR #2035 at merge commit `ea320c5e029393f876c69227492bec1b9de96177` and was
archived as `done`.

This follow-up does not supersede that packet. It narrows the handoff into the
decision surface the parent owner and reviewer need before implementation:

| Question | Follow-up position |
|---|---|
| Is the full aggregate stream implementable from current repo truth? | No. The aggregate event schema and upstream event sources are still not defined. |
| Can a narrower stream be implemented without inventing fields? | Only if the parent explicitly narrows to replaying existing `strategy_workshop_event` rows over SSE. |
| Can frontend bind to the requested aggregate stream now? | No. Frontend should remain on `POST /messages`, `GET /events`, and `GET /completeness` until the contract is promoted. |
| Does this packet change the blocker state by itself? | No. It gives the parent/reviewer a checklist for either keeping the blocker or narrowing the task. |

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | Sidecars may support delivery but must not outrank canonical L1/L2 truth. |
| `.orchestrator/task-briefs/ag_be_sw_004_sidecar_bff_handoff_followup_2.md` | Task asks for support materials only: BFF query gap, operator journey, and frontend handoff. |
| `.orchestrator/skills/worker-anchor-commit.md` | Commit scope must be explicit and task-owned. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo changes must go through task PR flow before `done`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | Follow-up task is active, owner `Codex`, reviewer `Codex2`, artifact path is this file. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-004` | Parent remains `blocked`, waiting for Codex clarification on missing stream contract truth. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-004-SIDECAR-BFF-HANDOFF` | Prior sidecar is archived `done`, with PR #2035 and merge commit `ea320c5e...`. |
| `support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF.md` | Prior packet records the full as-found route/schema/runtime gap and polling fallback. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/OPEN_DESIGN_GAPS_ROUND2_FOR_SD_TEAM_2026-06-21.md` | Gap C explicitly says typed aggregate workshop SSE event schema is missing and blocks `AG-BE-SW-004`. |
| `services/control-plane/openapi/agora_v1_1.openapi.yaml` and `agora_v1_2.openapi.yaml` | `/bff/agora/workshops/{workshop_id}/stream` is only `text/event-stream` with `schema: string`; description says strategy_workshop_event rows. |
| `services/control-plane/specs/agora/v3/workshop_event.schema.json` | Existing event rows define `message`, `version_created`, `version_selected`, `research_dispatched`, `consultation_started`, `status_changed`, `concluded`, and `archived`; no aggregate ack/completeness/progress payloads. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | Workshop `stream`, `versions`, `research-runs`, `consultations`, and `conclude` routes are still `501` stubs. |
| `services/control-plane/bff/agora/models.py` and `services/control-plane/bff/models.py` | `OPENCLAW_UPSTREAM_DEGRADED` is not a canonical Agora or shared BFF error enum. |
| `services/control-plane/bff/agora/servant/router.py` | Servant session streaming exists and accepts `Last-Event-ID`, but that does not define workshop aggregate payload semantics. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## Parent Disposition

`AG-BE-SW-004` should remain blocked for the full requested scope unless the
parent owner and reviewer explicitly narrow the task.

| Parent path | What changes before coding | Implementation boundary |
|---|---|---|
| Keep full aggregate stream | Add or promote a typed workshop aggregate stream contract covering message ack, completeness update, research progress, version activity, degraded state, audit/trace fields, and reconnect semantics. | Parent can implement against the promoted schema and tests. |
| Narrow to event-row SSE | Update parent acceptance to say the stream replays existing `strategy_workshop_event` rows only, equivalent to incremental `/events`. | Parent must not claim completeness/progress/version aggregate behavior beyond existing rows. |
| Defer runtime | Leave `AG-BE-SW-004` blocked and let frontend continue with polling and explicit not-ready states. | No BFF or frontend runtime changes from this sidecar. |

The current task text requires more than the current OpenAPI and schema bundle
define. Implementing the full scope now would require invented event names,
payloads, sources, or error semantics.

## BFF Query Gap Matrix

| Gap | Current repo truth | Parent decision needed |
|---|---|---|
| Event envelope | OpenAPI advertises generic `text/event-stream` as a string; no typed aggregate envelope exists. | Freeze SSE `event`, `id`, `retry`, cursor, metadata, and `data` shape before coding. |
| Message ack | `POST /messages` returns `202` with event id/sequence; there is no stream ack event or timer boundary. | Decide whether `<2s` measures HTTP `202`, first SSE event, persisted event row, or upstream enqueue. |
| Completeness updates | Completeness is a latest snapshot query; `workshop_event` has no `completeness_updated` event type. | Define whether completeness is emitted from snapshot writes, a projection table, or only polled. |
| Research progress | `POST /research-runs` is a `501` stub and research progress projection remains unspecified. | Define the progress source, event names, status enum, terminal states, and privacy/audit fields. |
| Version activity | `workshop_event` includes version row types, but BFF version routes are still `501` stubs. | Decide whether stream may replay future version rows only after version routes/store are live. |
| Reconnect | OpenAPI names `Last-Event-ID`, but does not say whether it is a sequence number, event id, or bounded replay token. | Freeze cursor format, replay window, duplicate handling, and forced resync behavior. |
| Degraded state | `OPENCLAW_UPSTREAM_DEGRADED` is a servant provider marker, not a workshop/BFF canonical error code. | Decide whether to promote it as a payload marker, map it to existing error envelopes, or keep it servant-only. |
| Audit/trace | Event schema requires `trace_id` and `request_id`, but current runtime paths can create rows with missing values. | Tighten write path requirements or define an audit projection before promising every stream event carries trace/audit. |
| Backpressure | There is no workshop-specific buffer, heartbeat, or slow-client policy. | Define bounded replay, heartbeat cadence, timeout, and overload/degraded behavior. |

## Frontend Handoff Matrix

Until the parent route contract is promoted or narrowed, execute-plans should
treat the workshop stream as unavailable in strict live mode.

| Frontend surface | Safe behavior now | Blocked behavior |
|---|---|---|
| Timeline | Poll `GET /bff/agora/workshops/{id}/events?after_sequence={last_seen}` and render server event rows. | Do not subscribe to aggregate event names such as `message_ack`, `completeness_update`, or `research_progress`. |
| Composer ack | Treat the `202` response from `POST /messages` as the only acknowledged write result. | Do not wait for a stream ack or show a stream-latency SLA. |
| Completeness rail | Poll `GET /bff/agora/workshops/{id}/completeness` after message submission or manual refresh. | Do not infer completeness changes from workshop event rows. |
| Research progress | Keep progress UI disabled, gated, or explicit not-ready. | Do not synthesize progress cards from missing research-run stream data. |
| Version cards | Keep compare/select flows gated on version route implementation. | Do not display stream version success from routes that still return `501`. |
| Degraded state | Surface BFF error envelopes and route availability directly. | Do not remap servant provider markers into canonical workshop errors. |
| Reconnect UX | If a narrow event-row stream later lands, resume only with the accepted cursor contract. | Do not assume `Last-Event-ID` is a sequence number without spec confirmation. |

Frontend should not hide `501`, `404`, or `503` as empty-success. In strict live
mode, unsupported workshop stream behavior should be visibly unavailable while
the existing polling journey remains usable.

## Operator Journey

### Current safe journey

```text
Operator opens a strategy workshop
  -> frontend loads workshop detail with GET /bff/agora/workshops/{id}
  -> frontend stores the returned ETag
  -> operator submits a message with POST /messages using If-Match and Idempotency-Key
  -> frontend treats the 202 response as the write acknowledgement
  -> frontend polls GET /events with after_sequence for timeline updates
  -> frontend polls GET /completeness for the latest completeness snapshot
  -> stream, research progress, version selection, consultations, and conclude remain not-ready
```

### Contract-ready journey after parent decisions

```text
Operator opens a strategy workshop
  -> frontend establishes the accepted SSE stream contract
  -> BFF emits only schema-backed events with trace/audit fields
  -> message ack, completeness, research progress, version, degraded, and terminal events each have promoted event types and data shapes
  -> Last-Event-ID resumes with the accepted cursor semantics
  -> frontend falls back to polling only according to the accepted degraded-mode rule
```

## Minimal Parent Acceptance If Narrowed

If the parent narrows to event-row SSE, update acceptance before implementation
so the delivered behavior remains truthful:

| Area | Narrow acceptance |
|---|---|
| Stream payload | SSE `data` is an existing `WorkshopEvent` row or documented envelope around that row. |
| Cursor | Cursor is explicitly `sequence_no` or `event_id`; tests cover reconnect without duplication. |
| Ack | First ack SLA is either removed or scoped to `POST /messages` HTTP `202`, not an aggregate SSE ack. |
| Completeness | Completeness remains polled unless a promoted event type exists. |
| Research | Research progress remains out of scope unless research-run projection source is promoted. |
| Version | Version events are replayed only when version rows are produced by live version routes/store. |
| Degraded | Use existing BFF error envelope or accepted event marker only. |
| Frontend | Client feature-detects stream and falls back to `/events` plus `/completeness`. |

## Reviewer Checklist

Codex2 review should verify this packet stays support-only:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact changes. |
| Canonical truth | No L1/L2 docs, OpenAPI, schema bundle, runtime code, or frontend code changed. |
| Factual alignment | Gap C still blocks the full aggregate stream; current router still returns `501` for `/stream`. |
| Prior packet alignment | This follow-up references and narrows the already merged sidecar, rather than replacing it. |
| Parent usefulness | The parent owner can decide between full design closure, event-row SSE narrowing, or continued block. |

Recommended reviewer approval command:

```bash
AI_NAME=Codex2 REVIEW_FILE=support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md \
  REVIEW_NOTES_ZH="Follow-up packet keeps AG-BE-SW-004 support-only, accurately narrows the already merged BFF handoff into parent disposition choices, frontend fail-closed guidance, and an event-row SSE narrowing checklist without changing canonical truth or runtime files." \
  ./scripts/ai-status.sh approve AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 \
  "Follow-up packet approved; parent owner can either keep AG-BE-SW-004 blocked for typed aggregate stream design or explicitly narrow it to event-row SSE replay."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh reopen AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 \
  "Describe the factual correction or missing handoff decision needed in the follow-up packet."
```

## Validation Plan

Commands to run from this task worktree before handoff:

```bash
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-004
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-004-SIDECAR-BFF-HANDOFF
python3 -m json.tool services/control-plane/specs/agora/v3/workshop_event.schema.json
python3 -m json.tool services/control-plane/specs/agora/strategy_completeness.schema.json
git diff --check -- support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md
```

No runtime tests are required for this follow-up because it changes only a
support artifact and intentionally does not touch BFF code.

## Support Boundary

- Primary packet artifact:
  `support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md`.
- Prior packet:
  `support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF.md`.
- No L1 canonical policy, L2 execution truth, OpenAPI, JSON schema, BFF router,
  store, error enum, governance implementation, or execute-plans frontend file
  is changed by this packet.

*Prepared by Codex for `AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`.*
