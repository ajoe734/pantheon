# CW-008 Consultation Workbench — Canonical Packet Family

## Header

- Packet family ID: `CW-008`
- Workbench: Consultation Workbench
- Phase origin: `BP5-WB-008`
- Lovable readiness: **not ready** — all four modules require net-new BFF routes and canonical write-path contracts; Lovable handoff must not open until BFF prerequisites below are satisfied
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
| `CW-01` | Consult Request | request composer, request detail, target selector, lifecycle state, request-to-session status | not ready | Wave 4 — 1st |
| `CW-02` | Debate Transcript | ordered conversation timeline, actor badges, inline evidence links, transcript replay, degraded partial-state handling | not ready | Wave 4 — 2nd |
| `CW-03` | Committee Board | committee queue or board view, participant roster, escalation reason, sponsor decision, synthesis summary, linked evidence | not ready | Wave 4 — 3rd |
| `CW-04` | Red-team Memo | findings summary, recommendation list, publish state, evidence drawer, downstream review handoff | not ready | Wave 4 — 4th |

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
| `POST /api/v1/consult/requests` | **missing** | create route; request body anchored to L3 design intent: `from_persona_id`, `target_type`, `target_ref`, `task`, `context_refs`, `priority`; net-new addition: `consultation_type` (from `PERSONA_RUNTIME_MODEL.md` session metadata — explicit BFF contract extension); must return `request_id` and initial `status: created` |
| `GET /api/v1/consult/requests` | **missing** | list route; must support `status`, `target_type`, `consultation_type`, `page_token`, `page_size` query params; must include `meta.surfaces.consult_request_list` |
| `GET /api/v1/consult/requests/:request_id` | **missing** | detail route; must expose `linked_session_id` (nullable until Persona Plane creates the session), `allowedActions.canCancel`, and `meta.surfaces.consult_request_detail` |
| `POST /api/v1/consult/requests/:request_id/cancel` | **missing** | cancel command; must be backed by `allowedActions.canCancel`; must not be invocable when `status` is already `completed` |
| `ConsultRequest` lifecycle contract | **missing** | the lifecycle states (`created → running → completed | canceled`) and the request-to-session handoff semantics must be promoted from L3 design intent to canonical BFF truth; the `linked_session_id` field must be explicitly defined |

### Packetization prerequisite

The `ConsultRequest` lifecycle (`created → running → completed | canceled`), the target taxonomy (`persona`, `committee`, `red_team`), and the request-to-session handoff contract must be promoted beyond L3 design intent to canonical BFF truth before a request-composer or request-detail screen can be packet-defined. `ConsultPolicy` and persona session creation (from `PERSONA_RUNTIME_MODEL.md` §6 and §14) remain canonical prerequisites.

### Lovable readiness gate

`false` — all five rows above must be resolved and field shapes locked before a screen spec or example payload can be created.

---

## CW-02 Debate Transcript

### Surface scope

- **Ordered conversation timeline**: chronological list of transcript events for a given `session_id`. Each event row shows `event_id`, `actor_id` (with a resolved actor label from the participant roster), `actor_role` (`requester`, `responder`, `committee_participant`), `event_type` (`message`, `evidence_attachment`, `outcome_signal`, `escalation_signal`), `body`, `emitted_at`, and inline evidence links if `event_type = evidence_attachment`.
- **Actor badges**: colored or labeled identity markers derived from the BFF-resolved participant roles — do not derive actor identity or role from the raw event stream client-side.
- **Inline evidence links**: when an event carries an `evidence_ref`, show a tappable link that navigates to the canonical evidence surface (telemetry, lineage, incident detail). The evidence link target is BFF-provided, not client-constructed.
- **Transcript replay**: a replay mode that steps through events in chronological order; pause, resume, and scrub controls. Replay state is ephemeral client state — the event sequence itself comes from the BFF.
- **Degraded partial-state handling**: when `meta.surfaces.transcript` is `partial` or `degraded`, show a non-dismissable partial-transcript banner with `last_event_at` and a note explaining that the transcript may be incomplete. Do not show a truncated transcript as if it were complete.
- **Degradation**: when `meta.surfaces.transcript` is `unavailable`, show the canonical unavailable banner. The transcript list must not render at all when the surface is unavailable.

### Backend gaps

| Route | Status | Notes |
|---|---|---|
| `GET /api/v1/consultations/:session_id/transcript` | **missing** | ordered event-stream read route; must return events sorted by `emitted_at`; must expose `meta.surfaces.transcript` with `ok | partial | degraded | unavailable` states; must include `last_event_at` for degraded-partial copy |
| Append-only transcript schema | **missing** | the `TranscriptEvent` object must be canonically defined: `event_id`, `session_id`, `actor_id`, `actor_role`, `event_type`, `body`, `evidence_ref` (nullable), `emitted_at`, `sequence_number`; the schema must guarantee strict append-only ordering via `sequence_number` |
| Actor labeling contract | **missing** | the BFF must resolve `actor_id` to a display label and role badge before serving the transcript; do not push this resolution to the client |
| Evidence attachment inline behavior | **missing** | when `event_type = evidence_attachment`, the BFF must provide a pre-resolved `evidence_link` in the event payload pointing to the canonical evidence surface (not a raw ref that the client must resolve) |

### Packetization prerequisite

The append-only `TranscriptEvent` schema, actor labeling contract, event ordering guarantee (`sequence_number`), and the degraded partial-transcript semantics must all be defined as canonical BFF truth before a transcript screen can be packet-defined. Depends on stable `ConsultRequest` and `SessionPersona` identity from `CW-01`.

### Lovable readiness gate

`false` — the transcript route, `TranscriptEvent` schema, actor labeling contract, and inline evidence behavior must all be implemented and field shapes locked before a screen spec can be opened.

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
| `GET /api/v1/committees` | **missing** | committee board list route; must support `quorum_state`, `consensus_state`, `page_token`, `page_size`; must include `meta.surfaces.committee_board` |
| `GET /api/v1/committees/:committee_id` | **missing** | committee board detail route; must expose `committee_ref`, `participant_roster[]`, `escalation_reason`, `quorum_state`, `consensus_state`, `synthesis_summary`, `linked_request_id`, `allowedActions.canRecordSponsorDecision` |
| Committee board projection | **missing** | canonical board projection for committee membership, referral state, `committee_ref` identity, sponsor selection, and conflict-resolution evidence linkage; currently defined only as policy in `MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md` — must be promoted to BFF contract |
| `POST /api/v1/operator/commands` (`RecordSponsorDecision`) | **missing** | sponsor decision write path; must be backed by `allowedActions.canRecordSponsorDecision`; must accept `committee_id`, `sponsor_decision` (`approved` | `rejected` | `conditional`), and `rationale_ref` |
| Synthesis summary shape | **missing** | the `synthesis_summary` object (`outcome`, `rationale_ref`, `evidence_refs[]`, `dissent_refs[]`) must be defined as a canonical BFF field; do not derive from raw participant outcomes client-side |

### Packetization prerequisite

The committee lifecycle states, participant and referral semantics, `committee_ref` identity, sponsor-selection flow, synthesis summary shape, and the evidence linkage contract must all be promoted from `MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md` policy into canonical BFF-facing truth before a committee board screen can be packet-defined. Depends on `CW-01` request identity and `CW-02` transcript event ordering.

### Lovable readiness gate

`false` — the committee board routes, board projection, sponsor decision write path, and synthesis summary shape must all be implemented and field shapes locked before a screen spec can be opened.

---

## CW-04 Red-team Memo

### Surface scope

- **Findings summary**: top-level view of a published red-team memo. Shows `memo_id`, `memo_type` (`red_team_findings`), `status` (`draft` | `published`, anchored to L3 schema `§6.10`), `author_ref`, `linked_request_id` (the originating `ConsultRequest`; corresponds to L3 FK field `request_id`), and a findings count badge. Note: `archived` is **not** present in the current L3 design intent; if `archived` lifecycle is needed it must be introduced as an explicit net-new contract decision and cannot be assumed as promoted L3 truth.
- **Recommendation list**: paginated list of recommendations from the red-team memo. Anchored to the L3 design intent (`recommendations_json`), the list is a plain string array in the current L3 shape. The recommendation display uses the L3-anchored plain recommendation list; per-recommendation severity tiers or workflow-status fields are not part of the current CW-04 scope and must not be added without an explicit net-new contract decision.
- **Status indicator**: reflects the `status` field from the BFF (L3 field name). When `draft`, show a draft watermark. When `published`, show publish date.
- **Evidence drawer**: expandable drawer for each recommendation showing the linked evidence objects (telemetry, lineage, consult session, incident case). Evidence links are BFF-resolved — do not construct evidence URLs client-side.
- **Downstream review handoff**: when the memo is published, the surface shows a downstream handoff CTA if `allowedActions.canInitiateGovernanceReview` is `true`. This navigates the operator to the Governance Workbench review queue with the memo pre-filtered. The CTA is hidden unless the `allowedActions` signal is present and truthy.
- **Memo list**: paginated list of all red-team memos filterable by `status`. Each row shows `memo_id`, `status`, `linked_request_id`, `author_ref`, and a recommendation count.
- **Degradation**: when `meta.surfaces.redteam_memo` is `degraded`, show the last-known memo state with a staleness banner. When `unavailable`, show the canonical unavailable banner with no memo content.

### Backend gaps

| Route | Status | Notes |
|---|---|---|
| `GET /api/v1/consult/memos` | **missing** | red-team memo list route; must support `status` filter (`draft | published`), `page_token`, `page_size`; must include `meta.surfaces.redteam_memo`; `consultation_type` filter and severity-tier summary are net-new additions and must not be treated as promoted L3 truth |
| `GET /api/v1/consult/memos/:memo_id` | **missing** | memo detail route; L3-anchored fields: `memo_id`, `linked_request_id` (L3: `request_id`), `memo_type`, `author_ref`, `summary`, `recommendations` (plain list per L3 `recommendations_json`), `evidence_refs`, `status`; must expose `allowedActions.canInitiateGovernanceReview`, `linked_session_id`, and `meta.surfaces.redteam_memo` |
| `ConsultMemo` read model | **missing** | the memo lifecycle (`draft → published`, anchored to L3 schema `§6.10`), the L3-defined recommendation and evidence-ref shapes, and the evidence-link contract must be promoted from L3 design intent to canonical BFF truth; `archived` state and per-recommendation severity or workflow status are **not** in the current L3 shape and must be introduced as explicit net-new contract decisions before appearing in BFF truth |
| Red-team session-to-memo mapping | **missing** | the relationship between a `red_team` session type, the originating `ConsultRequest`, and the published `ConsultMemo` must be defined as an explicit BFF contract — the UI cannot derive it from raw session data |
| `allowedActions.canInitiateGovernanceReview` signal | **missing** | backend-shaped authority signal that enables the downstream review handoff CTA; must be falsy unless the memo is published and governance routing is available |

### Packetization prerequisite

The `ConsultMemo` read model (published memo lifecycle, recommendation shape, and evidence-link contract), the red-team session-to-memo mapping, and the `allowedActions.canInitiateGovernanceReview` authority signal must all be defined as canonical BFF truth before a red-team memo screen can be packet-defined. Per-recommendation severity taxonomy is a future contract extension and is not a prerequisite for the current CW-04 packetization. Depends on `CW-01` request identity and `CW-02` transcript or session evidence semantics.

### Lovable readiness gate

`false` — the memo list and detail routes, `ConsultMemo` read model, session-to-memo mapping, and `allowedActions` signal must all be implemented and field shapes locked before a screen spec can be opened.

---

## Backend Gap Matrix

Each row is scoped to one or more modules. A module advances to Lovable-ready when all rows assigned to that module (and its upstream prerequisite modules) are resolved. See the Promotion Criteria section for the per-module gate definition.

| Route or contract | Module(s) | Gap type | Blocking what |
|---|---|---|---|
| `POST /api/v1/consult/requests` | CW-01 | missing write route | request creation form and lifecycle foundation; body fields anchored to L3: `from_persona_id`, `target_type`, `target_ref`, `task`, `context_refs`, `priority`; `consultation_type` is a net-new BFF contract extension |
| `GET /api/v1/consult/requests` | CW-01, CW-02, CW-03, CW-04 | missing read route | request list and cross-module request identity |
| `GET /api/v1/consult/requests/:request_id` | CW-01 | missing read route | request detail, `linked_session_id`, and `allowedActions.canCancel` |
| `POST /api/v1/consult/requests/:request_id/cancel` | CW-01 | missing write route | cancel command; gated by `allowedActions.canCancel` |
| `ConsultRequest` lifecycle contract | CW-01 | missing lifecycle contract | `created → running → completed | canceled` states; request-to-session handoff semantics; `linked_session_id` field definition |
| `GET /api/v1/consultations/:session_id/transcript` | CW-02 | missing read route | entire Debate Transcript module |
| `TranscriptEvent` schema | CW-02 | missing object contract | `event_id`, `sequence_number`, actor resolution, `event_type`, `evidence_ref`; blocks event ordering and replay |
| Actor labeling contract | CW-02 | missing BFF-side resolution | actor display labels and role badges; client must not resolve actor identity from raw participant refs |
| Evidence attachment inline behavior | CW-02 | missing BFF-side resolution | pre-resolved `evidence_link` in event payload; blocks inline evidence affordances |
| `GET /api/v1/committees` | CW-03 | missing read route | committee board list and filter surface |
| `GET /api/v1/committees/:committee_id` | CW-03 | missing read route | committee board detail, participant roster, escalation reason, `allowedActions.canRecordSponsorDecision` |
| Committee board projection | CW-03 | missing contract | `committee_ref` identity, quorum state, consensus state, and referral semantics must be promoted to BFF contract |
| `POST /api/v1/operator/commands` (`RecordSponsorDecision`) | CW-03 | missing write command | sponsor decision CTA; gated by `allowedActions.canRecordSponsorDecision` |
| Synthesis summary shape | CW-03 | missing object contract | `outcome`, `rationale_ref`, `evidence_refs[]`, `dissent_refs[]`; client must not derive from participant signals |
| `GET /api/v1/consult/memos` | CW-04 | missing read route | red-team memo list and filter surface |
| `GET /api/v1/consult/memos/:memo_id` | CW-04 | missing read route | memo detail, recommendations, `allowedActions.canInitiateGovernanceReview` |
| `ConsultMemo` read model | CW-04 | missing object contract | L3-anchored lifecycle: `draft → published` (per L3 schema §6.10); `archived` state is **not** in the current L3 design intent — if introduced it must be an explicit net-new contract decision, not assumed as promoted L3 truth; recommendation shape (plain list per L3 `recommendations_json`) and evidence-link contract must be promoted from L3 design intent to canonical BFF truth |
| Red-team session-to-memo mapping | CW-04 | missing contract | explicit relationship between `red_team` session, originating `ConsultRequest`, and published `ConsultMemo` |
| `allowedActions.canInitiateGovernanceReview` | CW-04 | missing authority signal | downstream review handoff CTA must be hidden unless this backend-shaped signal is present and truthy |

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

A Consultation Workbench module moves from **not ready** to **ready** (and may be handed to Lovable) when all of the following are true:

1. All BFF routes listed in that module's Backend Gaps table are implemented and have agreed field shapes.
2. The module's `meta.surfaces.*` staleness signals are defined and wired through to the canonical degradation banner (`PKT-005`).
3. All `allowedActions` authority signals for that module are backend-shaped and documented.
4. An example payload JSON exists for the module's primary read surface.
5. All upstream prerequisite modules are already Lovable-ready (per the dependency chain above).

No Consultation Workbench module should be handed to Lovable before its own criteria and all upstream criteria are met.

---

## Cross-Cutting Rules

### No shadow consultation model

The BFF must not maintain its own consultation state machine or synthesize outcomes from raw participant traffic. All consultation state flows from:

1. `SessionPersona` objects (canonical session state from `PERSONA_RUNTIME_MODEL.md`)
2. `ConsultPolicy` objects (canonical rules from `PERSONA_RUNTIME_MODEL.md` §6)
3. `SessionPersona.metadata.consultation.*` values written by the Persona Plane
4. Evidence references pointing to canonical objects (TelemetryEvent, LineageEdge, IncidentCase)
5. `ConsultRequest` objects (once promoted from L3 to canonical BFF truth)
6. `ConsultMemo` objects (once promoted from L3 to canonical BFF truth)

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
- Put absent write routes, transcript or event-stream contracts, committee board projections, `ConsultMemo` lifecycle contract, red-team session-to-memo mapping, and `allowedActions` authority signals in `Backend or contract dependencies`.

---

## Canonical References

- Backlog source: `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/pantheon-console-workbench-backlog.md` (Consultation Workbench section)
- Existing read surfaces: `services/control-plane/bff/CONSULTATION_SURFACE_CONTRACT.md` (CS-01 to CS-06)
- L1 policy basis: `PERSONA_RUNTIME_MODEL.md` (§2.2, §6, §13, §14), `MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md`
- Degradation substrate: `PKT-005` degradation banner and SSE substrate must be inherited by all four modules
- Governance handoff precedent: `PKT-001 Governance Review Queue` and `F-042 Promotion Review` define the `allowedActions` and `POST /api/v1/operator/commands` patterns that `CW-03` inherits for the sponsor decision write path
- Handoff directory: `docs/pantheon-handoffs/CW-008-consultation-workbench/`
- Dependent services: `BP5-SVC-003` (ApprovalDecision governance API), `BP5-SVC-012` (EvolutionDecision service), `BP5-SVC-014` (persona platform and consultation read surfaces)
