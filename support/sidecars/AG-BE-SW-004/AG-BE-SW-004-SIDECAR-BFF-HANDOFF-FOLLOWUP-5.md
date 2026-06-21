# AG-BE-SW-004 Sidecar Follow-up 5: Typed SSE Contract Landing — Disposition Update

| Field | Value |
|---|---|
| Task ID | `AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-SW-004` — Streaming workshop aggregate |
| Parent owner / reviewer | `Codex` / `Claude2` |
| Prepared by | `Claude` |
| Reviewer | `Claude2` |
| Date | 2026-06-21 |
| Mutates canonical truth | `false` |
| Status | In progress; pending Claude2 review |

This is a support-only follow-up packet. It does not modify L1 canonical truth,
L2 execution truth, OpenAPI/schema files, BFF runtime, registry/governance
implementation, or execute-plans frontend code. It records the delta from
follow-up 4: the typed workshop SSE aggregate contract is now designed and
partially merged; the parent task's status has shifted from `blocked` to `todo`
gated on `AG-XR-OPENAPI-004`.

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates tasks and does not override canonical architecture/policy truth. |
| `.orchestrator/task-briefs/ag_be_sw_004_sidecar_bff_handoff_followup_5.md` | This task is a sidecar support slice for BFF query gap, operator journey, and frontend handoff material only. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support changes require explicit scope and narrow commit discipline. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo changes must pass task commit, PR, merge, and owner closeout before `done`. |
| `AI_NAME=Claude ./scripts/ai-status.sh show AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` | Task is `in_progress`, owner `Claude`, reviewer `Claude2`. |
| `AI_NAME=Claude ./scripts/ai-status.sh show AG-BE-SW-004` | Parent is now `todo`, gated on `AG-XR-OPENAPI-004`; no longer `blocked`. |
| `AI_NAME=Claude ./scripts/ai-status.sh show AG-DES-SSE-001` | `review_approved`; deliverable `services/control-plane/specs/agora/v4/workshop_stream_event.schema.json` landed. |
| `AI_NAME=Claude ./scripts/ai-status.sh show AG-XR-OPENAPI-004` | `todo`; v1.3 bundle merge (OpenAPI + capability manifest + `bundle_index.v1_3.json` hashes) not yet merged to `dev`. |
| `services/control-plane/openapi/agora_v1_3.openapi.yaml` | v1.3 OpenAPI extension exists; `/bff/agora/workshops/{id}/stream` typed as `WorkshopStreamEvent` SSE. |
| `services/control-plane/specs/agora/v4/workshop_stream_event.schema.json` | 24 typed event types defined; full envelope with `event_id`, `event_type`, `aggregate_type`, `sequence_no`, `causal_parent_id`, `trace_id`, `idempotency_key`, `data_cutoff`, `visibility`, `payload_schema`, `payload`. |
| `services/control-plane/specs/agora/bundle_index.v1_3.json` | Bundle index with SHA256 hashes for v4 schemas, OpenAPI, and manifest; references `bundle_index.v1_2.json` as base. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/03_workshop_sse_contract.md` | §C1–C9 typed SSE aggregate contract: event catalog, latency SLA, cursor contract, replay window, heartbeat, error payload, and frontend rules. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/07_dispatch_unblock_matrix.md` | `AG-BE-SW-004` remains gated until SSE event schema/OpenAPI merged (i.e., after `AG-XR-OPENAPI-004` is done). |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/OPEN_DESIGN_GAPS_ROUND2_FOR_SD_TEAM_2026-06-21.md` | Gap C (workshop SSE aggregate event schema) is now addressed by design-closure-round2/03 + v4 schema. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | `/stream` route remains a `501` stub; no implementation has landed yet. |
| Prior packets: initial handoff, follow-up 2, follow-up 3, follow-up 4. | Prior dispositions still hold for the items that remain unimplemented; this packet records the typed contract landing as the key delta. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## Delta Summary Since Follow-up 4

| Area | Follow-up 4 finding | Follow-up 5 delta |
|---|---|---|
| Typed SSE event schema | Missing; only generic `text/event-stream` string schema existed. | `v4/workshop_stream_event.schema.json` is now present and `AG-DES-SSE-001` is `review_approved`. |
| v1.3 OpenAPI | Did not exist. | `agora_v1_3.openapi.yaml` exists referencing `WorkshopStreamEvent` schema via `$ref`. |
| Bundle index | Not present. | `bundle_index.v1_3.json` exists with SHA256 hashes for all v4 objects. |
| Design contract for SSE aggregate | No §C reference existed. | `design-closure-round2/03_workshop_sse_contract.md` defines §C1–C9 typed contract. |
| Cursor contract | Undefined; `Last-Event-ID` format unspecified. | Defined: `Last-Event-ID` carries `sequence_no` as a workshop-scoped integer; replay by sequence. |
| Ack SLA | No definition. | `p95 < 2s` targets the persisted command receipt (`workshop.message.accepted`), not LLM completion. |
| Replay window | Undefined. | Min 24 hours or last 10,000 events per workshop; `SSE_REPLAY_UNAVAILABLE` if outside window. |
| Heartbeat | Undefined. | Emit `stream.heartbeat` every 15 s; client declares degraded after 45 s without event or heartbeat. |
| Bundle merge (AG-XR-OPENAPI-004) | Did not exist as a task. | Exists as `todo`; v1.3 bundle merge is the remaining gate before `AG-BE-SW-004` can start. |
| Parent task status | `blocked`. | `todo`, gated on `AG-XR-OPENAPI-004`. |
| BFF router `/stream` | `501` stub. | Still `501` stub; no implementation yet. |

## Typed SSE Event Catalogue (from v4 schema)

The `workshop_stream_event.schema.json` defines the following `event_type` values:

| Category | Event types |
|---|---|
| Workshop lifecycle | `workshop.snapshot`, `workshop.concluded`, `workshop.archived` |
| Message & servant | `workshop.message.accepted`, `workshop.servant.response.started`, `workshop.servant.response.delta`, `workshop.servant.response.completed` |
| Completeness & guidance | `workshop.completeness.updated`, `workshop.next_question.updated` |
| Patch & version | `workshop.patch.proposed`, `workshop.patch.validated`, `workshop.version.created`, `workshop.version.selected` |
| Readiness | `workshop.readiness.updated` |
| Research | `research.plan.created`, `research.plan.approved`, `research.plan.cancelled`, `research.run.queued`, `research.run.progress`, `research.run.completed`, `research.run.failed` |
| Consultation | `consultation.started`, `consultation.completed` |
| Stream meta | `stream.heartbeat`, `stream.error` |

Each event carries the full envelope: `spec_version`, `event_id`, `event_type`, `aggregate_type` (`strategy_workshop`), `aggregate_id` (`workshop_id`), `sequence_no` (monotonic, starts at 1), `causal_parent_id` (nullable), `event_time`, `emitted_at`, `trace_id`, `request_id`, `idempotency_key`, `data_cutoff`, `visibility`, `payload_schema`, `payload`.

Ordering: per-aggregate monotonic; at-least-once delivery; consumers deduplicate by `event_id` and re-gap on out-of-order `sequence_no`.

## Disposition Update

The prior follow-up 4 recommendation was to keep `AG-BE-SW-004` blocked pending typed contract closure. That structural gap is now addressed: `design-closure-round2/03` defines the contract and `v4/workshop_stream_event.schema.json` provides the schema. The new status is **gated**, not blocked.

| Prior claim | Follow-up 4 status | Follow-up 5 status |
|---|---|---|
| Message acknowledgement; first ack < 2s | Blocked: no ack SLA or event type. | **Gated**: contract defines `workshop.message.accepted` with p95 < 2s persisted ack. Ready after `AG-XR-OPENAPI-004` merges. |
| Completeness update events | Blocked: completeness was snapshot only, no stream event. | **Gated**: `workshop.completeness.updated` is in the typed catalogue. Ready after `AG-XR-OPENAPI-004` merges. |
| Research progress events | Blocked: no projection or stream source. | **Gated**: `research.run.progress`, `research.run.queued`, etc. are typed. Projection/source impl still belongs to `AG-BE-RS-*`, but the event type is defined. Ready after `AG-XR-OPENAPI-004` merges and research facade lands. |
| Version activity events | Blocked: version routes `501`. | **Gated**: `workshop.version.created`, `workshop.version.selected` typed; version routes still `501` but the event type contract exists. Ready after version routes land. |
| Reconnect / `Last-Event-ID` | Blocked: cursor format unspecified. | **Gated**: cursor = `sequence_no` (integer); replay min 24h/10k events; `SSE_REPLAY_UNAVAILABLE` canonical error. Ready after `AG-XR-OPENAPI-004` merges. |
| `OPENCLAW_UPSTREAM_DEGRADED` on stream | Blocked: not in workshop error enums. | Still blocked: the v4 schema does not promote `OPENCLAW_UPSTREAM_DEGRADED` as a stream event or error code. `stream.error` is defined with `code`/`message`/`retryable`/`operation_ref` fields; a degradation code promotion is a separate contract decision. |
| Trace/audit on every stream event | Blocked: write paths incomplete. | **Gated**: `trace_id`, `request_id`, `idempotency_key` are required envelope fields. Audit write-path enforcement belongs to `AG-BE-SW-004` implementation task. |

## Unblock Gate

`AG-BE-SW-004` should not start BFF `/stream` implementation until:

1. `AG-DES-SSE-001` is `done` (currently `review_approved`, pending closeout). This finalizes `v4/workshop_stream_event.schema.json`.
2. `AG-XR-OPENAPI-004` is `done`. This merges the v1.3 OpenAPI, capability manifest, and `bundle_index.v1_3.json` (with final SHA256s) to `dev`.

Once both conditions are met, the parent owner may implement `/bff/agora/workshops/{id}/stream` against the merged schema and OpenAPI without opening a new blocker.

The parent should not implement:
- New event types beyond the 24 in the v4 catalogue.
- New payload fields not in the `workshop_stream_event.schema.json` envelope or the referenced `payload_schema` docs.
- A different cursor format than `sequence_no`.
- `OPENCLAW_UPSTREAM_DEGRADED` as a stream event (not promoted; use `stream.error` with a BFF-owned error code instead).

## Updated BFF Handoff Delta

Rows marked `gated` are implementable once `AG-XR-OPENAPI-004` is `done`. Rows marked `still blocked` need additional design/contract work.

### Safe / now-gated BFF path

```text
GET  /bff/agora/workshops/{id}/stream         — typed SSE; WorkshopStreamEvent envelope; gated on AG-XR-OPENAPI-004 done
GET  /bff/agora/workshops/{id}/completeness   — polling fallback; safe now; gated upgrade to completeness.updated event
GET  /bff/agora/workshops/{id}/events         — row replay; safe now
POST /bff/agora/workshops/{id}/messages       — 202 command receipt; safe now; ack SLA is HTTP 202 or workshop.message.accepted event
```

### Still-501-stub BFF path

```text
GET/POST /bff/agora/workshops/{id}/versions                        — version routes 501; workshop.version.* events gated on version impl
POST     /bff/agora/workshops/{id}/versions/{version_id}/select    — 501 stub
POST     /bff/agora/workshops/{id}/research-runs                   — old-style; plan-first facade (AG-BE-RS-001) replaces this
POST     /bff/agora/workshops/{id}/consultations                   — 501 stub; consultation.started/completed events defined
POST     /bff/agora/workshops/{id}/conclude                        — 501 stub; workshop.concluded event defined
```

### New v1.3 BFF path (from agora_v1_3.openapi.yaml; all gated on AG-XR-OPENAPI-004)

```text
GET/POST /bff/agora/workshops/{id}/patch-proposals                 — governs VersionPatchProposal lifecycle
POST     /bff/agora/workshops/{id}/patch-proposals/{id}/validate
POST     /bff/agora/workshops/{id}/patch-proposals/{id}/accept
POST     /bff/agora/workshops/{id}/patch-proposals/{id}/reject
POST     /bff/agora/workshops/{id}/version-comparisons             — multi-version diff
GET      /bff/agora/workshops/{id}/readiness                       — StrategyReadinessAssessment
POST     /bff/agora/workshops/{id}/readiness/reassess
GET      /bff/agora/workshops/{id}/cards                           — typed WorkshopCard projections (12 types)
GET/POST /bff/agora/research-plans/{id}                            — plan-first research facade
GET/POST /bff/agora/research-runs/{id}                             — run projections
GET/POST /bff/agora/trading-room                                   — read-only; no order routing
GET/POST /bff/agora/trading-intents/{id}/handoffs                  — request-only; no RuntimeBinding
```

The v1.3 routes are part of `agora_v1_3.openapi.yaml` and are not owned by `AG-BE-SW-004`. They are listed here for frontend orientation only.

## Updated Frontend Handoff Delta

| UI surface | Safe binding now | Gated binding (after AG-XR-OPENAPI-004 done) | Still blocked |
|---|---|---|---|
| Timeline / event feed | Poll `/events?after_sequence={last_seen}`; render server rows. | Subscribe to SSE stream; apply `WorkshopStreamEvent` by `event_type`. | None remaining for basic event feed. |
| Composer | Treat `POST /messages` `202` as write acknowledgement. | Treat `workshop.message.accepted` event as persisted ack; assert p95 < 2s. | None. |
| Completeness rail | Poll `/completeness` on submit or timer. | Upgrade to `workshop.completeness.updated` event for live rail. | None. |
| Research progress | Keep disabled. | Show `research.run.progress` / `research.run.queued` events when research facade (AG-BE-RS-*) lands. | Research facade projection not yet implemented. |
| Version UI | Keep disabled. | Show `workshop.version.created` / `workshop.version.selected` events after version routes land. | Version routes remain `501`. |
| Readiness gates | Not shown. | Read `/readiness`; show structured `StrategyReadinessAssessment` after AG-XR-OPENAPI-004 done. | Gate state machine impl pending. |
| Patch proposals | Not shown. | Enable after AG-XR-OPENAPI-004 + patch-proposal routes land. | `VersionPatchProposal` BFF routes not yet implemented. |
| Reconnect UX | Assume full reload on disconnect. | Use `Last-Event-ID` = last received `sequence_no`; reconnect on gap or timeout; snapshot-refresh on `SSE_REPLAY_UNAVAILABLE`. | None once cursor contract lands. |
| Degraded state | Surface `501`/`404`/`503` from `/stream` directly; do not hide as empty timeline. | Map `stream.error` to inline error indicator when stream route is live. | Do not emit/interpret `OPENCLAW_UPSTREAM_DEGRADED` from workshop stream. |
| Workshop cards | Not shown (free LLM markdown path is forbidden). | Render typed `WorkshopCard` projections from `/cards`; never infer card type from free text. | Card projection impl gated on AG-DES-CARD-001 and AG-XR-OPENAPI-004. |

## Parent Decision Memo

The parent owner `Codex` should not re-assess its blocker by reading only the sidecar packet. The correct unblock sequence is:

1. Confirm `AG-DES-SSE-001` is `done` (currently `review_approved`).
2. Confirm `AG-XR-OPENAPI-004` is `done` (currently `todo`).
3. Read `services/control-plane/specs/agora/v4/workshop_stream_event.schema.json` from the merged `dev` tree.
4. Read `services/control-plane/openapi/agora_v1_3.openapi.yaml` from the merged `dev` tree.
5. Implement `/bff/agora/workshops/{id}/stream` against those merged artifacts.

| Decision | Appropriate now | Notes |
|---|---|---|
| Keep gated | Yes — default posture until AG-XR-OPENAPI-004 is done. | Do not start stream implementation from pre-merge tree. |
| Start implementation | No — not yet. | Implementation must reference the merged v1.3 bundle hashes, not draft artifacts. |
| Keep aggregate ack/completeness/research-progress claims | Yes — all have now typed event types in the v4 catalogue. | ack = `workshop.message.accepted`; completeness = `workshop.completeness.updated`; research = `research.run.*`. |
| Cursor contract | `sequence_no` (integer) only; not `event_id`, not a string token. | Enforced by the replay contract in §C6. |
| `OPENCLAW_UPSTREAM_DEGRADED` | Do not promote on the workshop stream. | Use `stream.error` with an accepted BFF-owned code. |

## Reviewer Handoff

Claude2 review should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact changes. |
| Canonical truth | No L1/L2 docs, OpenAPI, schemas, runtime, registry/governance, or frontend files changed. |
| Factual alignment | `v4/workshop_stream_event.schema.json` exists and contains the 24 event types listed. `bundle_index.v1_3.json` exists. `AG-DES-SSE-001` is `review_approved`. `AG-XR-OPENAPI-004` is `todo`. Parent `AG-BE-SW-004` is `todo`. BFF `/stream` is still `501` stub. |
| Delta accuracy | Follow-up 5 accurately represents the state shift from `blocked` to `gated`. |
| Prior packet alignment | Follow-up 5 composes with the initial packet and follow-ups 2/3/4 without superseding them. |

Recommended reviewer approval command for reviewer `Claude2`:

```bash
AI_NAME=Claude2 REVIEW_FILE=support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md \
  REVIEW_NOTES_ZH="Follow-up 5 stays support-only and records the disposition shift: typed SSE aggregate contract now exists in v4 schema and design-closure-round2/03, AG-DES-SSE-001 is review_approved, and AG-BE-SW-004 is gated on AG-XR-OPENAPI-004 rather than structurally blocked. BFF and frontend handoff delta is updated accordingly. No canonical truth or runtime files changed." \
  ./scripts/ai-status.sh approve AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-5 \
  "Support-only disposition update approved; typed SSE contract has landed and AG-BE-SW-004 is now gated on AG-XR-OPENAPI-004 bundle merge, not structurally blocked."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh reopen AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-5 \
  "Describe the factual correction, schema state discrepancy, or parent disposition gap needed before approval."
```

## Validation Run

Commands run from this task worktree:

```bash
AI_NAME=Claude ./scripts/ai-status.sh show AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-5
AI_NAME=Claude ./scripts/ai-status.sh show AG-BE-SW-004
AI_NAME=Claude ./scripts/ai-status.sh show AG-DES-SSE-001
AI_NAME=Claude ./scripts/ai-status.sh show AG-XR-OPENAPI-004
python3 -m json.tool services/control-plane/specs/agora/v4/workshop_stream_event.schema.json
python3 -m json.tool services/control-plane/specs/agora/bundle_index.v1_3.json
rg -n "[ \t]+$" support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md
git diff --check -- support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md
```

Observed results:

| Command | Result |
|---|---|
| `ai-status.sh show AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` | PASS; task is `in_progress`, owner `Claude`, reviewer `Claude2`. |
| `ai-status.sh show AG-BE-SW-004` | PASS; parent is `todo`, gated on `AG-XR-OPENAPI-004`. |
| `ai-status.sh show AG-DES-SSE-001` | PASS; `review_approved`, artifact is `v4/workshop_stream_event.schema.json`. |
| `ai-status.sh show AG-XR-OPENAPI-004` | PASS; `todo`, owner `Claude2`, not yet merged. |
| `json.tool` on `workshop_stream_event.schema.json` | PASS; schema parses; 24 `event_type` enum values confirmed. |
| `json.tool` on `bundle_index.v1_3.json` | PASS; bundle index parses; `workshop_stream_event.schema.json` SHA256 entry present. |
| `rg -n "[ \t]+$" ...FOLLOWUP-5.md` | PASS; no trailing whitespace matches. |
| `git diff --check -- ...FOLLOWUP-5.md` | PASS; no whitespace errors. |

No BFF runtime tests are required for this support packet because it changes
only a support artifact and intentionally does not touch runtime code, schemas,
OpenAPI, or frontend code.

## Support Boundary

- Primary artifact:
  `support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md`.
- Prior packets:
  `support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF.md`,
  `support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md`,
  `support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md`, and
  `support/sidecars/AG-BE-SW-004/AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md`.
- No canonical docs, OpenAPI, JSON schema, BFF router/store, error enum,
  governance implementation, or execute-plans frontend file is changed.

*Prepared by Claude for `AG-BE-SW-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-5`.*
