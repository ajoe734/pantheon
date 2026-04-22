# CW-008 Consultation Workbench — Canonical Packet Family

## Header

- Packet family ID: `CW-008`
- Workbench: Consultation Workbench
- Phase origin: `BP5-WB-008`
- Lovable readiness: **partially opened** — `CW-01` routes are live and the returned UI cycle is under Pantheon follow-up review; `CW-03` list/detail routes are live and may partial-activate; `CW-02` Debate Transcript and `CW-04` Red-team Memo are now contract-ready with pending BFF implementation
- Overview packet status: `PKT-consultation-workbench` remains the truthful landing surface; `CW-01-FOUNDATION-001` now has a route-live contract bundle for the request lifecycle, while later modules still depend on additional Consultation BFF surfaces
- Recommended wave: Wave 4 — after Operator Console (Waves 1–2), Persona Workbench (Waves 1–2), and Governance / Evolution workbench packetization are settled
- Owner: Claude
- Reviewer: Codex

---

## Objective

Give operators and personas one coherent workbench for initiating structured consultations, reviewing ordered debate transcripts, tracking committee board state, and reading published red-team findings. All data and CTA authority must come from the Pantheon BFF — no client-side state synthesis, no shadow consultation models, no locally derived committee verdicts.

---

## Existing Pantheon Support (pre-conditions)

Before any Consultation Workbench module can be packetized, the following canonical artifacts must be treated as known truth:

| Artifact | Location | What it defines |
|---|---|---|
| `CONSULTATION_SURFACE_CONTRACT.md` | `services/control-plane/bff/` | Six read surfaces (CS-01 to CS-06): consultation list, detail, participants, outcome, evidence, and consult policy; degraded-behavior matrix and operator journey flows |
| `PERSONA_RUNTIME_MODEL.md` §2.2, §6, §13, §14 | L1 policy | `consult` and `committee` session types; `ConsultPolicy` model; three consultation roles; `SessionPersona.metadata.consultation.*` fields |
| `MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md` | L1 policy | Committee escalation conditions, sponsor selection, synthesis rules, quorum and consensus semantics, and `committee_ref` lifecycle |
| L3 design docs | `Pantheon_總索引版系統分析文件.md`, `Pantheon_API_Service_Contract_設計版.md`, `Pantheon_資料表_Schema_設計版.md` | `ConsultRequest` and `ConsultMemo` object names, `POST /api/v1/consult/requests`, `GET /api/v1/consult/requests/:id`, and memo lifecycle as design intent — these are not canonical BFF truth yet |

The existing consultation read surfaces (CS-01 to CS-06) cover outcome and evidence reads. They do **not** define a workbench IA, request-write path, ordered transcript surface, committee board projection, or red-team memo read model. Those are the gaps this packet family addresses.

---

## Module Inventory

| Module ID | Module name | Screen / surface scope | Lovable readiness | Wave order |
|---|---|---|---|---|
| `CW-01` | Consult Request | request composer, request detail, target selector, lifecycle state, request-to-session status | contract-ready; BFF route live; current UI cycle under review | Wave 4 — 1st |
| `CW-02` | Debate Transcript | ordered conversation timeline, actor badges, inline evidence links, transcript replay, degraded partial-state handling | contract-ready; pending BFF | Wave 4 — 2nd |
| `CW-03` | Committee Board | committee queue or board view, participant roster, escalation reason, sponsor decision, synthesis summary, linked evidence | partial-ready; route-live with transcript-dependent full handoff gate | Wave 4 — 3rd |
| `CW-04` | Red-team Memo | findings summary, recommendation list, publish state, evidence drawer, downstream review handoff | contract-ready; pending BFF | Wave 4 — 4th |

---

## CW-01 Consult Request

### Surface scope

- **Request composer**: form for creating a new consultation request. Fields anchored to the L3 `ConsultRequest` design intent (`Pantheon_API_Service_Contract_設計版.md §5.3.2`, `Pantheon_資料表_Schema_設計版.md §6.9`): `from_persona_id` (initiating persona identity), `target_type` (`persona` | `committee` | `red_team`), `target_ref` (target persona or committee identity), `task` (question or problem description), `context_refs` (array of typed context references: `{type, id}`), and `priority` (`low | normal | high | critical`). **Net-new BFF contract addition**: `consultation_type` (`pre_deployment`, `risk_review`, `macro_regime_shift`, `incident_response`, `policy_change`, `general`) is promoted from `SessionPersona.metadata.consultation.consultation_type` in `PERSONA_RUNTIME_MODEL.md` and is not present in the current L3 API shape; it is explicitly called out here as a new field required for BFF routing and workbench filtering, and must be documented as a deliberate contract extension. The submission target is `POST /api/v1/consult/requests`.
- **Target selector**: the target persona or committee is selected from a backend-provided list — do not hardcode persona or committee identities client-side. Selection determines which `ConsultPolicy` rules apply.
- **Request detail**: full view of a submitted `ConsultRequest` showing `request_id`, `status` (`created | running | completed | canceled`), `from_persona_id`, `target_type`, `target_ref`, `task`, `context_refs`, `priority`, `consultation_type` (net-new BFF addition — see request composer note), `created_at`, `completed_at`, `linked_session_id` (once the Persona Plane has created the session), and `allowedActions` (cancel).
- **Request-to-session status**: a status indicator showing whether the Persona Plane has created a `SessionPersona` for this request. The indicator reads from `linked_session_id` — the BFF does not infer session creation from elapsed time.
- **Lifecycle state machine**: `created → running → completed | canceled`. Each state is a backend-shaped field on the `ConsultRequest` object — do not derive lifecycle state client-side.
- **Request list**: paginated list of all consultation requests filterable by `status`, `target_type`, and `consultation_type`. Each row shows `request_id`, `status`, `target_type`, `consultation_type`, `created_at`, and a link to the request detail view.
- **Degradation**: when `meta.surfaces.consult_request_list` or `meta.surfaces.consult_request_detail` is `degraded` or `unavailable`, show the canonical non-dismissable degradation banner (inherited from `PKT-005`). Never show "no requests" as authoritative when the surface is degraded — that is a dangerous false negative during governance-critical moments.

### Backend gaps

| Route | Status | Notes |
|---|---|---|
| `POST /api/v1/consult/requests` | **live** | create route is implemented and verified against the published body/response shape, including `consultation_type`, `status: created`, and `request_to_session_status: pending_session` |
| `GET /api/v1/consult/requests` | **live** | list route is implemented with the published filters, pagination envelope, and `meta.surfaces.consult_request_list` |
| `GET /api/v1/consult/requests/:request_id` | **live** | detail route is implemented with `linked_session_id`, `request_to_session_status`, `session_handoff`, `allowedActions.canCancel`, and `meta.surfaces.consult_request_detail` |
| `POST /api/v1/consult/requests/:request_id/cancel` | **live** | cancel route is implemented and returns the published canceled envelope with `allowedActions.canCancel: false` |
| `ConsultRequest` lifecycle contract | **live** | the `created → running → completed | canceled` lifecycle, request-to-session handoff semantics, `linked_session_id`, and `session_handoff` fields are now served by the current BFF implementation |

### Packetization prerequisite

The `ConsultRequest` lifecycle (`created → running → completed | canceled`), the target taxonomy (`persona`, `committee`, `red_team`), and the request-to-session handoff contract must be promoted beyond L3 design intent to canonical BFF truth before a request-composer or request-detail screen can be packet-defined. `ConsultPolicy` and persona session creation (from `PERSONA_RUNTIME_MODEL.md` §6 and §14) remain canonical prerequisites.

### Published contract bundle

- BFF contract: `docs/bff/CW-01-consult-request.md`
- Screen spec: `docs/screens/CW-01-consult-request.md`
- Example payload: `docs/examples/CW-01-consult-request.json`
- Frontend change spec: `docs/pantheon-handoffs/CW-01-consult-request/FRONTEND_CHANGE_SPEC.md`
- Contract-ready response: `.coordination/responses/CW-01-consult-request-contract-ready.yaml`
- Lovable UI task: `.coordination/responses/CW-01-consult-request-lovable-ui-task.yaml`

### Lovable readiness gate

`ready` — the request lifecycle, request-to-session handoff semantics, screen spec, example payload, and frontend handoff bundle are aligned with the live BFF implementation. The current CW-01 frontend return is still under Pantheon follow-up review, so any further front loop must republish a truthful `ui-done` + `frontend-feedback` bundle instead of reopening route-live truth.

---

## CW-02 Debate Transcript

### Surface scope

- **Ordered conversation timeline**: chronological list of transcript events for a given `session_id`. Each event row shows `event_id`, `sequence_no`, `event_type`, `event_time`, `actor.display_name`, `actor.role`, `content.text`, and attached `evidence_refs[]`. Replay and ordering must follow `sequence_no`, not guessed timestamp order.
- **Actor badges**: colored or labeled identity markers derived from the BFF-provided `actor.actor_type`, `actor.actor_id`, `actor.display_name`, and `actor.role` tuple — do not derive actor identity or role from the raw event stream client-side.
- **Inline evidence links**: when an event carries `evidence_refs[]`, the transcript surface may render backend-resolved evidence navigation metadata or delegated links from the canonical evidence surface. The client must not construct evidence URLs from storage refs or guessed path templates.
- **Transcript replay**: a replay mode that steps through events in chronological order; pause, resume, and scrub controls. Replay state is ephemeral client state — the event sequence itself comes from the BFF.
- **Degraded partial-state handling**: when `meta.surfaces.transcript` is `partial`, show a non-dismissable partial-transcript banner explaining that enrichment is incomplete while the append-only event stream remains trustworthy. If ordering integrity, event continuity, or transcript completeness fails, the surface must move to `degraded` rather than pretending a trustworthy partial replay exists.
- **Degradation**: when `meta.surfaces.transcript` is `unavailable`, show the canonical unavailable banner. The transcript list must not render at all when the surface is unavailable.

### Backend gaps

| Route | Status | Notes |
|---|---|---|
| `GET /api/v1/consultations/:session_id/transcript` | **contract-published** | ordered transcript route, pagination, `from_sequence_no`, `meta.staleness`, and `meta.surfaces.transcript.state` are now ratified in `docs/bff/CW-02-debate-transcript.md`; BFF implementation is still pending |
| Append-only transcript schema | **ratified** | the canonical `TranscriptEvent` object now defines `sequence_no`, nested `actor`, nested `content`, `evidence_refs[]`, append-only ordering, and transcript integrity rules |
| Actor labeling contract | **ratified** | canonical actor identity comes from upstream transcript truth via `actor.actor_type` and `actor.actor_id`; the BFF may enrich only `actor.display_name` |
| Evidence attachment inline behavior | **ratified** | transcript events now carry canonical `evidence_refs[]`; any display-link enrichment must preserve backend-owned evidence identity and may not be fabricated client-side |

### Packetization prerequisite

The append-only `TranscriptEvent` schema, actor identity rule, event ordering guarantee (`sequence_no`), and the partial-vs-degraded transcript semantics are now ratified as canonical BFF truth. The remaining gate is implementing the published transcript route family against stable `ConsultRequest` and `SessionPersona` identity from `CW-01`.

### Published contract bundle

- BFF contract: `docs/bff/CW-02-debate-transcript.md`
- Example payload: `docs/examples/CW-02-debate-transcript.json`

### Lovable readiness gate

`pending-bff` — the transcript contract bundle is now ratified and implementation may proceed, but Lovable should wait until the transcript route is live.

---

## CW-03 Committee Board

### Surface scope

- **Committee queue or board view**: a list of active committee sessions derived from `session_type = committee`. Each board row shows `committee_id` (the `committee_ref` from `MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md`), `escalation_reason`, `quorum_state` (`insufficient`, `quorum_met`, `supermajority_met`), `consensus_state` (`pending`, `reached`, `failed`, `sponsor_required`), `linked_request_id`, and `started_at`.
- **Participant roster**: list of committee participants for a given `committee_id`. Each row shows `participant_id`, `persona_id`, `role` (`committee_participant`, `sponsor`), `status` (active, voted, abstained), and the participant's contributed `outcome_signal` if available (showing `approved`, `rejected`, or `conditional`). Do not synthesize a committee verdict from participant signals — the verdict comes from the BFF's `synthesis_summary`.
- **Escalation reason panel**: displays the escalation trigger that caused committee formation (`trigger_rule`, `forbidden_solo_action`, or `escalation_path` from `SessionPersona.metadata.consultation`).
- **Sponsor decision surface**: when `consensus_state = sponsor_required`, shows the assigned sponsor and the `allowedActions.canRecordSponsorDecision` authority signal. The decision write path targets `POST /api/v1/operator/commands` with a `RecordSponsorDecision` command. The CTA is hidden unless `allowedActions.canRecordSponsorDecision` is `true`.
- **Synthesis summary**: the BFF-composed synthesis output for the committee session (`outcome`, `rationale_ref`, `evidence_refs[]`, `dissent_refs[]`). Never synthesize a verdict from raw participant votes client-side.
- **Linked evidence drawer**: evidence refs from `CS-05` that are linked to the committee session. Each evidence link resolves to the canonical surface.
- **Degradation**: when `meta.surfaces.committee_board` is `degraded` or `unavailable`, show the canonical non-dismissable degradation banner. Never hide the quorum or consensus state — showing a false "no consensus" during active committee deliberation is a governance risk.

### Backend gaps

| Route | Status | Notes |
|---|---|---|
| `GET /api/v1/committees` | **live** | committee board list route is implemented and returns `meta.surfaces.committee_board`; partial activation may consume it now |
| `GET /api/v1/committees/:committee_id` | **live** | committee board detail route is implemented with participant roster, escalation context, synthesis summary, and `allowedActions.canRecordSponsorDecision` |
| Committee board projection | **live** | canonical board projection for committee membership, referral state, `committee_ref` identity, sponsor selection, and conflict-resolution evidence linkage is now served by the BFF |
| `POST /api/v1/operator/commands` (`RecordSponsorDecision`) | **live** | sponsor decision write path is implemented and gated by `allowedActions.canRecordSponsorDecision` |
| CW-02 transcript dependency | **blocking full handoff** | `CW-03` may partial-activate now, but full transcript-linked production handoff still waits on `CW-02` ordered transcript truth |

### Packetization prerequisite

The committee lifecycle states, participant and referral semantics, `committee_ref` identity, sponsor-selection flow, synthesis summary shape, and the evidence linkage contract are now live in the BFF. `CW-03` may partial-activate before `CW-02` is fully live, but full transcript-linked production handoff still depends on `CW-02` ordered transcript truth.

### Published contract bundle

- BFF contract: `docs/bff/CW-03-committee-board.md`
- Example payload: `docs/examples/CW-03-committee-board.json`

### Lovable readiness gate

`partial-ready` — the committee board list/detail routes, board projection, `RecordSponsorDecision` command, and synthesis summary shape are live. Partial activation is allowed now; full production handoff still waits on `CW-02`.

---

## CW-04 Red-team Memo

### Surface scope

- **Findings summary**: top-level view of a red-team memo. Shows `memo_id`, `memo_type` (`red_team`), `status`, `lifecycle_state`, `author_ref`, `linked_request_id`, `linked_session_id`, and a findings count badge. The v1 lifecycle remains `draft -> published`; supersession is modeled by relationship metadata instead of a new primary lifecycle state.
- **Recommendation list**: paginated list of recommendations from the red-team memo. `recommendations[]` remains a plain string array in v1. Per-recommendation severity tiers or workflow-status fields are still out of scope unless a later explicit contract decision adds them.
- **Status indicator**: reflects the `status` field from the BFF (L3 field name). When `draft`, show a draft watermark. When `published`, show publish date.
- **Evidence drawer**: expandable drawer for each recommendation showing the linked evidence objects (telemetry, lineage, consult session, incident case). Evidence links are BFF-resolved — do not construct evidence URLs client-side.
- **Downstream review handoff**: when the memo is published and governance routing is valid, the surface shows a downstream handoff CTA if `allowedActions.canInitiateGovernanceReview` is `true`. This signal is backend-owned and depends on memo lifecycle, target validity, authority, duplicate-review suppression, and evidence availability.
- **Session-to-memo mapping**: memo detail must expose an explicit `session_to_memo_mapping` object tying the source consultation session and transcript to the published memo. The UI must not derive this mapping from raw session data.
- **Memo list**: paginated list of all red-team memos filterable by `status`. Each row shows `memo_id`, `status`, `linked_request_id`, `author_ref`, and a recommendation count.
- **Degradation**: when `meta.surfaces.redteam_memo` is `degraded`, show the last-known memo state with a staleness banner. When `unavailable`, show the canonical unavailable banner with no memo content.

### Backend gaps

| Route | Status | Notes |
|---|---|---|
| `GET /api/v1/consult/memos` | **contract-published** | memo list route, pagination envelope, `status` filter, `items[]`, `meta.staleness`, and `meta.surfaces.redteam_memo.state` are now ratified in `docs/bff/CW-04-redteam-memo.md`; BFF implementation is still pending |
| `GET /api/v1/consult/memos/:memo_id` | **contract-published** | memo detail route, lifecycle metadata, plain-string `recommendations[]`, evidence refs, and downstream handoff signal are now ratified; BFF implementation is still pending |
| `ConsultMemo` read model | **ratified** | v1 lifecycle (`draft -> published`), recommendation shape, optional supersession metadata, and degradation rules are now canonical BFF truth |
| Red-team session-to-memo mapping | **ratified** | the `session_to_memo_mapping` object now defines how source session, transcript, and memo identities relate |
| `allowedActions.canInitiateGovernanceReview` signal | **ratified** | governance-handoff authority now has an explicit seven-condition backend-owned gate |

### Packetization prerequisite

The `ConsultMemo` read model, `session_to_memo_mapping`, and the `allowedActions.canInitiateGovernanceReview` authority signal are now defined as canonical BFF truth. The remaining gate is implementing the published memo route family against stable `CW-01` request identity and `CW-02` transcript or session evidence semantics. Per-recommendation severity taxonomy remains a future extension and is not required for the current CW-04 packetization.

### Published contract bundle

- BFF contract: `docs/bff/CW-04-redteam-memo.md`
- Example payload: `docs/examples/CW-04-redteam-memo.json`

### Lovable readiness gate

`pending-bff` — the memo contract bundle is now ratified and implementation may proceed, but Lovable should wait until the memo route family is live.

---

## Backend Gap Matrix

Each row is scoped to one or more modules. A module advances to Lovable-ready when all rows assigned to that module (and its upstream prerequisite modules) are resolved. See the Promotion Criteria section for the per-module gate definition.

| Route or contract | Module(s) | Gap type | Blocking what |
|---|---|---|---|
| `POST /api/v1/consult/requests` | CW-01 | contract-ready — BFF route live | request creation form and lifecycle foundation; body and initial response shape defined in `docs/bff/CW-01-consult-request.md` are now implemented |
| `GET /api/v1/consult/requests` | CW-01, CW-02, CW-03, CW-04 | contract-ready — BFF route live for request identity | request list and cross-module request identity |
| `GET /api/v1/consult/requests/:request_id` | CW-01 | contract-ready — BFF route live | request detail, `linked_session_id`, `request_to_session_status`, and `allowedActions.canCancel` are now implemented |
| `POST /api/v1/consult/requests/:request_id/cancel` | CW-01 | contract-ready — BFF route live | cancel command; gated by `allowedActions.canCancel` |
| `ConsultRequest` lifecycle contract | CW-01 | contract-ready — runtime wiring live | `created → running → completed | canceled` states; request-to-session handoff semantics; `linked_session_id` and `session_handoff` field definition |
| `GET /api/v1/consultations/:session_id/transcript` | CW-02 | contract published — BFF implementation pending | transcript route, pagination, and surface-state semantics are now ratified and ready for implementation |
| `TranscriptEvent` schema | CW-02 | ratified | event ordering, actor identity, nested content, and evidence-ref semantics are locked |
| Actor labeling contract | CW-02 | ratified | actor identity is upstream-owned; BFF may only enrich display labels |
| Evidence attachment inline behavior | CW-02 | ratified | transcript evidence refs and enrichment boundaries are now locked |
| `GET /api/v1/committees` | CW-03 | live | committee board list and filter surface are implemented |
| `GET /api/v1/committees/:committee_id` | CW-03 | live | committee board detail, participant roster, escalation reason, and `allowedActions.canRecordSponsorDecision` are implemented |
| Committee board projection | CW-03 | live | `committee_ref` identity, quorum state, consensus state, and referral semantics are served by the current BFF |
| `POST /api/v1/operator/commands` (`RecordSponsorDecision`) | CW-03 | live | sponsor decision command is wired to canonical operator authority |
| CW-02 ordered transcript truth | CW-03 | blocking full handoff | `CW-03` may partial-activate now, but transcript drill-down and full production handoff still depend on `CW-02` |
| `GET /api/v1/consult/memos` | CW-04 | contract published — BFF implementation pending | memo list route is now ratified and ready for implementation |
| `GET /api/v1/consult/memos/:memo_id` | CW-04 | contract published — BFF implementation pending | memo detail route and review-handoff surface are now ratified and ready for implementation |
| `ConsultMemo` read model | CW-04 | ratified | lifecycle, recommendation, supersession, and degradation semantics are locked |
| Red-team session-to-memo mapping | CW-04 | ratified | mapping semantics are locked |
| `allowedActions.canInitiateGovernanceReview` | CW-04 | ratified | governance-handoff authority rules are locked |

---

## Internal Ordering and Dependency Chain

| Position | Module | Why this order | Upstream dependency within workbench |
|---|---|---|---|
| Wave 4 — 1st | `CW-01 Consult Request` | establishes the foundational request object, target taxonomy (`persona`, `committee`, `red_team`), and request-to-session handoff that every later Consultation module references; `linked_request_id` appears in every downstream module | none — can start when Wave 4 opens |
| Wave 4 — 2nd | `CW-02 Debate Transcript` | defines the ordered conversation evidence model that both committee and red-team views rely on for evidence chain and actor identity; the `TranscriptEvent` schema must be stable before committee or memo surfaces can cite session evidence | `CW-01`: `ConsultRequest` identity and `linked_session_id` must be live |
| Wave 4 — 3rd | `CW-03 Committee Board` | adds policy-driven committee state, sponsor decision, and synthesis outputs on top of the request and transcript evidence chain; the committee projection requires stable session identity from `CW-01` and the event ordering contract from `CW-02` | `CW-01` request identity; `CW-02` transcript event ordering and actor identity |
| Wave 4 — 4th | `CW-04 Red-team Memo` | publishes finalized adversarial findings against the same request and evidence chain; memo semantics build on request identity and session evidence, but the memo object is independent of committee board state | `CW-01` request identity; `CW-02` transcript or session evidence semantics |

---

## Promotion Criteria

A Consultation Workbench module moves from **contract-ready / pending-bff** or **partial-ready** to **ready** (and may be handed to Lovable) when all of the following are true:

1. All BFF routes listed in that module's Backend Gaps table are implemented and have agreed field shapes.
2. The module's `meta.surfaces.*` staleness signals are defined and wired through to the canonical degradation banner (`PKT-005`).
3. All `allowedActions` authority signals for that module are backend-shaped and documented.
4. An example payload JSON exists for the module's primary read surface.
5. All upstream prerequisite modules are already Lovable-ready (per the dependency chain above).

No Consultation Workbench module should be handed to Lovable before its own criteria and all upstream criteria are met.

`CW-03` special rule: partial activation is allowed before `CW-02` is fully live, but only for read-only / sponsor-status / outcome-summary surfaces that do not invent transcript drill-down.

---

## Cross-Cutting Rules

### No shadow consultation model

The BFF must not maintain its own consultation state machine or synthesize outcomes from raw participant traffic. All consultation state flows from:

1. `SessionPersona` objects (canonical session state from `PERSONA_RUNTIME_MODEL.md`)
2. `ConsultPolicy` objects (canonical rules from `PERSONA_RUNTIME_MODEL.md` §6)
3. `SessionPersona.metadata.consultation.*` values written by the Persona Plane
4. Evidence references pointing to canonical objects (TelemetryEvent, LineageEdge, IncidentCase)
5. `ConsultRequest` objects (once promoted from L3 to canonical BFF truth)
6. `ConsultMemo` objects (now promoted to canonical BFF truth via `docs/bff/CW-04-redteam-memo.md`)

### No client-side synthesis

The UI must never:
- Derive actor identity or role from raw event stream without BFF resolution
- Infer committee consensus or committee verdict from raw participant vote signals
- Construct a committee synthesis summary from participant outcome fields
- Resolve evidence links from raw `evidence_ref` identifiers without BFF pre-resolution
- Determine sponsor assignment or quorum state without BFF-provided fields

### Consultation write authority

This packet family adds two write routes at the BFF layer:

- `POST /api/v1/consult/requests` — request creation; body fields anchored to L3 (`from_persona_id`, `target_type`, `target_ref`, `task`, `context_refs`, `priority`) with `consultation_type` as a net-new BFF contract extension.
- `POST /api/v1/consult/requests/:request_id/cancel` — request cancellation; must be gated by `allowedActions.canCancel`; must not be invocable when `status` is already `completed`.

Session creation, outcome recording, and evidence attachment remain Persona Plane responsibility.

- The sponsor decision command (`RecordSponsorDecision`) follows the `POST /api/v1/operator/commands` pattern established by `PKT-001` and `F-042`.

### Degradation banner inheritance

All four modules must inherit the canonical degradation banner from `PKT-005`. The banner must be non-dismissable. Individual surface staleness states (`meta.surfaces.consult_request_detail`, `meta.surfaces.transcript`, `meta.surfaces.committee_board`, `meta.surfaces.redteam_memo`) must be passed through from the BFF — never derived locally.

### Relationship to existing consultation read surfaces

`CS-01` to `CS-06` (from `CONSULTATION_SURFACE_CONTRACT.md`) remain canonical for consultation list, detail, participants, outcome, and evidence reads. This packet family does not replace or fork those surfaces. The Consultation Workbench builds on top of them by adding:
- `CW-01`: request-write path and request lifecycle (not covered by CS-**)
- `CW-02`: ordered transcript surface (CS-02 detail does not provide event ordering or replay)
- `CW-03`: committee board projection (CS-03 participants does not provide quorum, synthesis, or sponsor state)
- `CW-04`: red-team memo read model (CS-04 outcome does not provide memo lifecycle, recommendation list, or downstream handoff)

---

## Separation Rules

When authoring packet language for these modules:

- Put request-composer copy, transcript timeline layouts, board widget copy, memo presentation wording, degraded-state copy, and CTA label work in `Missing screen-spec work`.
- Put pending route implementations, transcript delivery behavior, committee board production handoff limits, memo read-model wiring, and `allowedActions` authority signals in `Backend or contract dependencies`.

---

## Canonical References

- Backlog source: `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/pantheon-console-workbench-backlog.md` (Consultation Workbench section)
- Existing read surfaces: `services/control-plane/bff/CONSULTATION_SURFACE_CONTRACT.md` (CS-01 to CS-06)
- L1 policy basis: `PERSONA_RUNTIME_MODEL.md` (§2.2, §6, §13, §14), `MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md`
- Degradation substrate: `PKT-005` degradation banner and SSE substrate must be inherited by all four modules
- Governance handoff precedent: `PKT-001 Governance Review Queue` and `F-042 Promotion Review` define the `allowedActions` and `POST /api/v1/operator/commands` patterns that `CW-03` inherits for the sponsor decision write path
- Handoff directory: `docs/pantheon-handoffs/CW-008-consultation-workbench/`
- Dependent services: `BP5-SVC-003` (ApprovalDecision governance API), `BP5-SVC-012` (EvolutionDecision service), `BP5-SVC-014` (persona platform and consultation read surfaces)
