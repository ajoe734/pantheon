# AG-BE-SW-004 Sidecar Follow-up 3: Parent Handoff Packet

| Field | Value |
|---|---|
| Task ID | `AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-SW-004` - Streaming workshop aggregate |
| Parent owner / reviewer | `Codex2` / `Codex` |
| Prepared by | `Codex2` |
| Reviewer | `Codex` |
| Date | 2026-06-21 |
| Mutates canonical truth | `false` |
| Status | Ready for Codex review |

This is a support-only packet for the parent owner and reviewer. It does not
modify L1 canonical truth, L2 execution truth, OpenAPI/schema files, BFF
runtime, registry/governance implementation, or execute-plans frontend code.
It turns the existing sidecar evidence into a parent absorption decision record:
keep `AG-BE-SW-004` blocked for typed aggregate stream design, or explicitly
narrow it to existing event-row SSE replay before implementation.

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates work; sidecars must not override canonical architecture/policy truth. |
| `.orchestrator/task-briefs/ag_be_sw_004_sidecar_bff_handoff_followup_3.md` | This task asks for support artifacts only, with no canonical truth changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support changes require narrow commits with explicit scope. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo changes must go through task commit, PR, review, and owner closeout. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` | Current task is active, owner `Codex2`, reviewer `Codex`, artifact path is this file. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-SW-004` | Parent is still `blocked`, waiting for `Codex`, because the aggregate stream contract is underspecified. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | Prior follow-up is archived `done`; PR #2039 merged at `6e94344b57ab2cced7f54ce0d7ec7067e45f098c`. |
| `support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF.md` | Initial sidecar records the as-found BFF route/schema/runtime gap and frontend polling fallback. |
| `support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` | Follow-up 2 turns the blocker into parent disposition choices and fail-closed frontend guidance. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/OPEN_DESIGN_GAPS_ROUND2_FOR_SD_TEAM_2026-06-21.md` | Gap C says the workshop SSE aggregate event schema is missing: message ack, completeness update, research progress, version event, first-ack latency, and long-task semantics. |
| `services/control-plane/openapi/agora_v1_1.openapi.yaml` and `agora_v1_2.openapi.yaml` | `/bff/agora/workshops/{workshop_id}/stream` is `text/event-stream` with `schema: string`; the description says `strategy_workshop_event` rows. |
| `services/control-plane/specs/agora/v3/workshop_event.schema.json` | Existing rows cover `message`, version links, research dispatch refs, consultation start, status, conclusion, and archive; they do not define aggregate ack/completeness/progress event payloads. |
| `services/control-plane/specs/agora/strategy_completeness.schema.json` | Completeness is a snapshot object, not a stream event contract. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | Versions, research-runs, consultations, conclude, and stream routes remain registered `501` stubs. |
| `services/control-plane/bff/agora/models.py` and `services/control-plane/bff/models.py` | `OPENCLAW_UPSTREAM_DEGRADED` is absent from Agora and shared BFF error enums. |
| `services/control-plane/bff/agora/servant/router.py` | `OPENCLAW_UPSTREAM_DEGRADED` exists only as a servant provider degraded marker. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## Current Decision State

`AG-BE-SW-004` should not implement the originally requested aggregate stream
from current repo truth. The blocker is factual, not merely scheduling:

| Parent requirement | Current repo truth | Consequence |
|---|---|---|
| Stream message ack event | `POST /messages` returns `202` with event id/sequence; no SSE ack event or measurement boundary exists. | Do not claim first SSE ack under 2 seconds until the event contract says what counts. |
| Completeness update event | Completeness exists as latest snapshot query; `WorkshopEvent.event_type` has no completeness event. | Frontend must poll `/completeness` or wait for a promoted stream event. |
| Research progress event | `POST /research-runs` is `501`; progress projection/source remains unspecified. | No truthful research progress stream can be emitted by this route family today. |
| Version events | Row schema has `version_created` and `version_selected`, but version routes are `501`. | Runtime stream must not promise version activity before version store/routes are live. |
| Reconnect contract | OpenAPI names `Last-Event-ID` but does not define cursor format or replay window. | Do not assume sequence number vs event id without parent/spec decision. |
| Degraded behavior | `OPENCLAW_UPSTREAM_DEGRADED` is servant-provider-only, not a workshop/BFF error enum. | Do not promote or emit that token from workshop stream without explicit contract change. |
| Trace/audit guarantee | Event schema requires `trace_id` and `request_id`, but current runtime paths are not tightened for aggregate stream output. | Stream trace/audit claims need a promoted envelope or tightened write path. |

## Reviewer Decision Needed

The parent reviewer should return one explicit disposition for
`AG-BE-SW-004`:

| Option | Decision text | Parent effect |
|---|---|---|
| Keep blocked | Full aggregate stream remains blocked pending typed schema/source design. | Parent owner does not implement BFF runtime yet. |
| Narrow scope | Parent acceptance is narrowed to replay existing `strategy_workshop_event` rows over SSE. | Parent can implement event-row SSE only, with no ack/completeness/research aggregate claims. |
| Request SD closure | A design task/spec patch must define the aggregate stream envelope and sources. | Parent waits for design closure, then implements against promoted truth. |

Any other path risks inventing event names, payload fields, cursor behavior, or
error semantics that the task acceptance explicitly forbids.

## Parent Absorption Checklist

If the parent stays blocked:

| Check | Required parent record |
|---|---|
| Blocker reason | Missing typed aggregate schema and upstream source map. |
| Waiting for | `Codex` reviewer/spec clarification. |
| Frontend posture | Use polling journey and not-ready stream state. |
| Next unblock artifact | Additive design/spec packet or explicit parent acceptance narrowing. |

If the parent narrows to event-row SSE:

| Check | Required parent record |
|---|---|
| Acceptance rewrite | Say stream emits existing `WorkshopEvent` rows or a documented envelope around those rows. |
| Cursor | Pick exactly one cursor: `sequence_no` or `event_id`; test reconnect without duplicates. |
| Ack SLA | Remove SSE ack SLA or scope it to HTTP `202` from `POST /messages`. |
| Completeness | Keep `/completeness` polling unless a promoted event type lands. |
| Research | Keep research progress out of scope until research-run projection is live. |
| Version | Emit version rows only after version routes/store produce them. |
| Degraded | Use existing BFF envelope or an explicitly promoted event marker only. |
| Privacy | Never stream raw private content; only server-owned row fields or refs. |

## BFF Handoff Boundary

The current safe BFF boundary is:

```text
GET  /bff/agora/workshops
POST /bff/agora/workshops
GET  /bff/agora/workshops/{id}
POST /bff/agora/workshops/{id}/messages
GET  /bff/agora/workshops/{id}/events?after_sequence={n}
GET  /bff/agora/workshops/{id}/completeness
```

The current not-ready BFF boundary is:

```text
GET/POST /bff/agora/workshops/{id}/versions
POST     /bff/agora/workshops/{id}/versions/{version_id}/select
POST     /bff/agora/workshops/{id}/research-runs
POST     /bff/agora/workshops/{id}/consultations
POST     /bff/agora/workshops/{id}/conclude
GET      /bff/agora/workshops/{id}/stream
```

The support packet does not ask BFF implementers to remove those stubs. It asks
the parent task to either keep them blocked or narrow `/stream` to a field-backed
row replay contract.

## Frontend Handoff Boundary

Until parent disposition changes, execute-plans Strategy Workshop UI should use
the fail-closed journey:

| UI surface | Safe binding | Do not bind yet |
|---|---|---|
| Timeline | Poll `/events?after_sequence={last_seen}` and render returned event rows. | Aggregate `message_ack`, `completeness_update`, or `research_progress` events. |
| Composer | Treat `POST /messages` `202` response as the current write acknowledgement. | Stream-latency SLA or first-stream-event acknowledgement. |
| Completeness rail | Poll `/completeness` after submit, refresh, or timer. | Implied completeness stream deltas. |
| Research progress | Show not-ready/gated state. | Progress cards from missing research-run stream source. |
| Version UI | Keep compare/select flows gated on route implementation. | Successful version stream events while version routes are `501`. |
| Degraded state | Surface BFF route errors and unsupported state explicitly. | Mapping servant provider markers into workshop canonical errors. |
| Reconnect | Wait for accepted cursor semantics. | Assuming `Last-Event-ID` means sequence number without spec. |

Frontend must not treat `501`, `404`, or `503` from the stream route as an
empty-success state. In strict live mode, unsupported stream behavior should be
visible while polling remains usable.

## Suggested Parent Status Language

If Codex keeps the parent blocked, the parent status should stay materially
equivalent to:

```text
AG-BE-SW-004 remains blocked for full aggregate stream implementation. Current
OpenAPI only promises generic text/event-stream rows, workshop stream/research/
version routes are still 501 stubs, and no promoted event schema defines
message ack, completeness update, research progress, version activity,
OPENCLAW_UPSTREAM_DEGRADED workshop behavior, or Last-Event-ID replay semantics.
Parent may proceed only after typed design closure or explicit narrowing to
strategy_workshop_event row replay.
```

If Codex narrows the parent, the status should say the parent is no longer
claiming the original aggregate stream and should name the exact dropped claims:
SSE ack SLA, completeness stream delta, research progress stream, version stream
unless backed by rows, and workshop-level degraded token behavior.

## Validation Run

Commands run from this task worktree:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-3
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-SW-004
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2
python3 -m json.tool services/control-plane/specs/agora/v3/workshop_event.schema.json
python3 -m json.tool services/control-plane/specs/agora/strategy_completeness.schema.json
rg -n "[ \t]+$" support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md
```

Observed results:

| Command | Result |
|---|---|
| `ai-status.sh show AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` | PASS; task is active, owner `Codex2`, reviewer `Codex`, artifact path matches this file. |
| `ai-status.sh show AG-BE-SW-004` | PASS; parent remains `blocked`, waiting for `Codex`, with blocker text matching the missing aggregate stream contract. |
| `ai-status.sh show AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | PASS; prior follow-up is archived `done`, with PR #2039 merged. |
| `json.tool` on `workshop_event` and `strategy_completeness` | PASS; both schemas parse. |
| `rg -n "[ \t]+$" ...FOLLOWUP-3.md` | PASS; no trailing whitespace matches were returned. |

No BFF runtime tests are required for this support packet because it does not
touch runtime code, schemas, OpenAPI, or frontend code.

## Reviewer Handoff

Codex review should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact changes. |
| Canonical truth | No L1/L2 docs, OpenAPI, schemas, runtime, registry/governance, or frontend files changed. |
| Factual alignment | Gap C, OpenAPI, router stubs, schemas, and error enums still support the blocker conclusion. |
| Parent usefulness | The packet gives a concrete decision path: blocked, narrowed event-row SSE, or SD design closure. |
| Prior packet alignment | Follow-up 3 composes with the initial packet and follow-up 2, without superseding either. |

Recommended reviewer approval command:

```bash
AI_NAME=Codex REVIEW_FILE=support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md \
  REVIEW_NOTES_ZH="Follow-up 3 stays support-only and gives the AG-BE-SW-004 parent owner/reviewer a concrete disposition packet: keep the full aggregate stream blocked, narrow to existing event-row SSE, or request design closure. It does not change canonical truth or runtime files." \
  ./scripts/ai-status.sh approve AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-3 \
  "Support-only parent handoff packet approved; parent AG-BE-SW-004 can remain blocked pending typed aggregate stream design or be explicitly narrowed to event-row SSE replay."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Codex ./scripts/ai-status.sh reopen AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-3 \
  "Describe the factual correction, missing source, or parent disposition gap needed before approval."
```

## Support Boundary

- Primary artifact:
  `support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md`.
- Prior packets:
  `support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF.md` and
  `support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md`.
- No canonical docs, OpenAPI, JSON schema, BFF router/store, error enum,
  governance implementation, or execute-plans frontend file is changed.

*Prepared by Codex2 for `AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-3`.*
