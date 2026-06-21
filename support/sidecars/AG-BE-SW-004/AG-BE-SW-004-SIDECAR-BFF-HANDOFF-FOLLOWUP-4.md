# AG-BE-SW-004 Sidecar Follow-up 4: BFF/Frontend Disposition Memo

| Field | Value |
|---|---|
| Task ID | `AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-SW-004` - Streaming workshop aggregate |
| Parent owner / reviewer | `Codex2` / `Codex` |
| Prepared by | `Codex2` |
| Reviewer | `Codex` |
| Date | 2026-06-21 |
| Mutates canonical truth | `false` |
| Status | Ready for Codex review |

This is a support-only follow-up packet. It does not modify L1 canonical truth,
L2 execution truth, OpenAPI/schema files, BFF runtime, registry/governance
implementation, or execute-plans frontend code. It gives the parent owner and
reviewer a concise disposition memo for `AG-BE-SW-004`: the full aggregate
workshop stream remains blocked by missing typed contract truth, while a narrow
event-row SSE route is implementable only after the parent explicitly drops the
aggregate claims.

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates tasks and does not override canonical architecture/policy truth. |
| `.orchestrator/task-briefs/ag_be_sw_004_sidecar_bff_handoff_followup_4.md` | This task is a sidecar support slice for BFF query gap, operator journey, and frontend handoff material only. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support changes require explicit scope and narrow commit discipline. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo changes must pass task commit, PR, merge, and owner closeout before `done`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` | Current task is active, owner `Codex2`, reviewer `Codex`, and artifact path is this file. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-SW-004` | Parent is still `blocked`, waiting for `Codex`, because the aggregate stream contract is underspecified. |
| `support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF.md` | Initial packet records as-found route/schema/runtime gaps and polling fallback. |
| `support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` | Follow-up 2 turns the blocker into parent disposition choices and frontend fail-closed guidance. |
| `support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md` | Follow-up 3 gives a parent absorption checklist for blocked vs narrowed handling. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/OPEN_DESIGN_GAPS_ROUND2_FOR_SD_TEAM_2026-06-21.md` | Gap C still says the workshop SSE aggregate event schema is missing: message ack, completeness update, research progress, version event, first-ack latency, and long-task semantics. |
| `services/control-plane/openapi/agora_v1_1.openapi.yaml` and `agora_v1_2.openapi.yaml` | `/bff/agora/workshops/{workshop_id}/stream` is `text/event-stream` with generic string schema and description as `strategy_workshop_event` rows. |
| `services/control-plane/specs/agora/v3/workshop_event.schema.json` | Existing row envelope covers message/version/research-dispatched/consultation/status/conclude/archive rows, but not aggregate ack, completeness, or progress payloads. |
| `services/control-plane/specs/agora/strategy_completeness.schema.json` | Completeness is a snapshot object, not a stream event contract. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | Versions, research-runs, consultations, conclude, and stream routes remain registered `501` stubs. |
| `services/control-plane/bff/agora/models.py`, `services/control-plane/bff/models.py`, and `services/control-plane/bff/agora/servant/router.py` | `OPENCLAW_UPSTREAM_DEGRADED` remains a servant provider degraded marker, not a shared BFF or workshop error enum. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## Disposition Summary

`AG-BE-SW-004` should keep its blocker for the full requested aggregate stream
until the reviewer or SD lane supplies typed contract closure. The current repo
truth supports only one narrow implementation path: stream already-defined
`strategy_workshop_event` rows over SSE, equivalent to an incremental
`GET /events` replay. That narrow path is not the same as the parent's current
acceptance.

| Parent claim | Current support status | Required disposition |
|---|---|---|
| Message acknowledgement over workshop SSE, first ack under 2 seconds | `POST /messages` returns `202` with event id/sequence; no SSE ack event or timer boundary exists. | Keep blocked, or rewrite acceptance so the ack is the HTTP `202` response only. |
| Completeness update events over the stream | Completeness is a latest snapshot query and has no `WorkshopEvent.event_type`. | Keep polling `/completeness`, or promote a typed completeness stream event before coding. |
| Research progress events | `POST /research-runs` is `501`; no progress projection/source is specified for this route family. | Keep progress UI gated until research facade projection and payload contract are promoted. |
| Version activity events | Row schema has `version_created`/`version_selected`, but version routes are still `501`. | Emit only future row-backed version events after version routes/store are live. |
| Reconnect support | OpenAPI names `Last-Event-ID`, but cursor format and replay window are unspecified. | Pick exactly one cursor contract before implementation: `sequence_no`, `event_id`, or another promoted token. |
| `OPENCLAW_UPSTREAM_DEGRADED` workshop behavior | Token exists only in servant provider streaming code and is absent from shared/workshop error enums. | Do not emit it from workshop stream unless promoted as a payload marker or mapped through an accepted error envelope. |
| Trace/audit fields on every stream event | Row schema requires `trace_id`/`request_id`, but current runtime creation paths are not a complete aggregate-stream audit contract. | Tighten write paths or define a stream envelope before promising every aggregate event has audit fields. |

## Parent Decision Memo

The reviewer should choose one of these explicit dispositions before the parent
owner writes BFF runtime code:

| Decision | Meaning | Parent next step |
|---|---|---|
| Keep blocked | Full aggregate stream remains out of scope because typed event payloads and upstream sources are missing. | Leave `AG-BE-SW-004` blocked with waiting_for `Codex` or a named SD/spec closure task. |
| Narrow to event-row SSE | Parent drops aggregate ack/completeness/research-progress claims and implements only `strategy_workshop_event` row replay over SSE. | Update parent acceptance before implementation, then test replay, auth, privacy, and reconnect against the row schema. |
| Request design closure | A spec/design packet promotes a typed aggregate stream envelope and source map. | Parent waits, then implements exactly the promoted schema and tests each aggregate event type. |

No sidecar packet should be treated as the contract change. The contract change,
if desired, belongs in the parent/spec lane and must be explicit.

## BFF Handoff Delta

The current safe BFF read/write path remains:

```text
GET  /bff/agora/workshops
POST /bff/agora/workshops
GET  /bff/agora/workshops/{id}
POST /bff/agora/workshops/{id}/messages
GET  /bff/agora/workshops/{id}/events?after_sequence={n}
GET  /bff/agora/workshops/{id}/completeness
```

The current not-ready BFF path remains:

```text
GET/POST /bff/agora/workshops/{id}/versions
POST     /bff/agora/workshops/{id}/versions/{version_id}/select
POST     /bff/agora/workshops/{id}/research-runs
POST     /bff/agora/workshops/{id}/consultations
POST     /bff/agora/workshops/{id}/conclude
GET      /bff/agora/workshops/{id}/stream
```

If the parent narrows to event-row SSE, the BFF implementation should treat the
SSE `data` body as either the existing `WorkshopEvent` row or a documented
wrapper around that row. It should not introduce new aggregate event names,
payload fields, degraded enums, or cursor semantics in the runtime patch.

## Frontend Handoff Delta

Until parent disposition changes, execute-plans should keep Strategy Workshop
live behavior on the polling path and show stream features as unavailable rather
than silently successful.

| UI surface | Safe binding now | Blocked binding |
|---|---|---|
| Timeline | Poll `/events?after_sequence={last_seen}` and render returned server rows. | Aggregate `message_ack`, `completeness_update`, or `research_progress` event names. |
| Composer | Treat `POST /messages` `202` as the write acknowledgement. | Stream-latency SLA or first-stream-event acknowledgement. |
| Completeness rail | Poll `/completeness` after submit, refresh, or timer. | Implied completeness deltas from row stream. |
| Research progress | Keep disabled, gated, or explicit not-ready. | Progress cards from missing research-run stream data. |
| Version UI | Keep compare/select flows gated until version routes/store are live. | Successful version stream events while version routes return `501`. |
| Degraded state | Surface BFF route failure/unsupported state directly. | Remapping servant provider markers into workshop canonical errors. |
| Reconnect UX | Wait for accepted cursor semantics. | Assuming `Last-Event-ID` is sequence number or event id without spec. |

Frontend strict live mode should not hide `501`, `404`, or `503` from
`/stream` as an empty timeline. Polling remains the only currently safe operator
journey.

## Absorption Checklist For Parent Owner

Before changing `services/control-plane/bff/agora/strategy_workshop/router.py`,
the parent owner should record one of:

| If parent stays blocked | Required record |
|---|---|
| Blocker reason | Missing typed aggregate stream schema and source map. |
| Waiting for | `Codex` reviewer/spec clarification, or an explicit SD closure task. |
| Frontend posture | Polling journey plus visible not-ready stream state. |
| Next unblock artifact | Promoted contract/spec packet or parent acceptance narrowing. |

| If parent narrows | Required record |
|---|---|
| Acceptance | Stream emits existing `WorkshopEvent` rows or a documented envelope around those rows. |
| Cursor | Exactly one cursor is accepted and tested for reconnect without duplicates. |
| Ack SLA | Removed from SSE scope or explicitly measured by HTTP `202`. |
| Completeness | Remains polled unless a promoted event type lands. |
| Research | Remains out of scope until a projection/source contract lands. |
| Version | Emits only row-backed version events after version routes/store are live. |
| Degraded behavior | Uses accepted BFF envelope or explicitly promoted event marker only. |
| Privacy | Streams refs/redacted fields only; raw private content never appears. |

## Reviewer Handoff

Codex review should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact changes. |
| Canonical truth | No L1/L2 docs, OpenAPI, schemas, runtime, registry/governance, or frontend files changed. |
| Factual alignment | Gap C, OpenAPI, router stubs, schemas, and error enums still support the blocker conclusion. |
| Parent usefulness | The packet gives a concise disposition memo plus BFF/frontend handoff delta. |
| Prior packet alignment | Follow-up 4 composes with the initial packet and follow-ups 2/3 without superseding them. |

Recommended reviewer approval command for reviewer `Codex`:

```bash
AI_NAME=Codex REVIEW_FILE=support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md \
  REVIEW_NOTES_ZH="Follow-up 4 stays support-only and confirms the AG-BE-SW-004 full aggregate workshop stream remains blocked by missing typed event contract truth. It gives a concise parent decision memo and BFF/frontend handoff delta without changing canonical truth or runtime files." \
  ./scripts/ai-status.sh approve AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-4 \
  "Support-only BFF/frontend disposition packet approved; parent AG-BE-SW-004 can remain blocked pending typed aggregate stream design or be explicitly narrowed to event-row SSE replay."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Codex ./scripts/ai-status.sh reopen AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-4 \
  "Describe the factual correction, missing source, or parent disposition gap needed before approval."
```

## Validation Run

Commands run from this task worktree:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-4
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-SW-004
python3 -m json.tool services/control-plane/specs/agora/v3/workshop_event.schema.json
python3 -m json.tool services/control-plane/specs/agora/strategy_completeness.schema.json
rg -n "[ \t]+$" support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md
git diff --check -- support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md
```

Observed results:

| Command | Result |
|---|---|
| `ai-status.sh show AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` | PASS; task is active, owner `Codex2`, reviewer `Codex`, artifact path matches this file. |
| `ai-status.sh show AG-BE-SW-004` | PASS; parent remains `blocked`, waiting for `Codex`, with blocker text matching the missing aggregate stream contract. |
| `json.tool` on `workshop_event` and `strategy_completeness` | PASS; both schemas parse. |
| `rg -n "[ \t]+$" ...FOLLOWUP-4.md` | PASS; no trailing whitespace matches were returned. |
| `git diff --check -- ...FOLLOWUP-4.md` | PASS; no whitespace errors reported. |

No BFF runtime tests are required for this support packet because it changes
only a support artifact and intentionally does not touch runtime code, schemas,
OpenAPI, or frontend code.

## Support Boundary

- Primary artifact:
  `support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md`.
- Prior packets:
  `support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF.md`,
  `support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md`,
  and `support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md`.
- No canonical docs, OpenAPI, JSON schema, BFF router/store, error enum,
  governance implementation, or execute-plans frontend file is changed.

*Prepared by Codex2 for `AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-4`.*
