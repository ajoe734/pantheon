# CW-01 Consult Request BFF Contract

## Status

**Contract published** — the request identity, lifecycle semantics, and request-to-session handoff shape are now the definitive implementation target for the Pantheon BFF. UI work must not start until Pantheon confirms the routes are live and returning this field shape.

Task: `CW-01-FOUNDATION-001`

## Purpose

Provide one canonical request surface for the Consultation Workbench so operators can create, list, inspect, and cancel consultation requests without inventing request lifecycle, session linkage, or cancel authority in the browser.

## Routes

### Create consult request

- `POST /api/v1/consult/requests`

Required request body:

- `from_persona_id` — initiating persona identity
- `target_type` — `"persona"` | `"committee"` | `"red_team"`
- `target_ref` — target persona or committee identity
- `task` — the question, scenario, or problem statement
- `context_refs[]` — array of typed refs:
  - `type` — `"artifact"` | `"deployment_plan"` | `"incident"` | `"lineage_edge"` | `"telemetry_ref"` | `"note"`
  - `id` — canonical identifier for the linked object
- `priority` — `"low"` | `"normal"` | `"high"` | `"critical"`
- `consultation_type` — `"pre_deployment"` | `"risk_review"` | `"macro_regime_shift"` | `"incident_response"` | `"policy_change"` | `"general"`

Required response fields:

- `request_id`
- `status` — must return `"created"` for a newly accepted request
- `created_at`
- `linked_session_id` — nullable
- `request_to_session_status` — must return `"pending_session"` on create
- `allowedActions.canCancel`

### List consult requests

- `GET /api/v1/consult/requests`

Supported query params:

- `status`
- `target_type`
- `consultation_type`
- `page_token`
- `page_size`

Required response fields:

- `data[]`
  - `request_id`
  - `status`
  - `from_persona_id`
  - `target_type`
  - `target_ref`
  - `task_summary`
  - `priority`
  - `consultation_type`
  - `created_at`
  - `linked_session_id` — nullable
  - `request_to_session_status`
  - `allowedActions.canCancel`
- `page_info.next_page_token`
- `page_info.total`
- `meta.snapshot_at`
- `meta.surfaces.consult_request_list` — `"fresh"` | `"stale"` | `"degraded"` | `"unavailable"`

### Get consult request detail

- `GET /api/v1/consult/requests/{request_id}`

Required response fields:

- `request_id`
- `status`
- `from_persona_id`
- `target_type`
- `target_ref`
- `task`
- `context_refs[]`
- `priority`
- `consultation_type`
- `created_at`
- `completed_at` — nullable
- `canceled_at` — nullable
- `linked_session_id` — nullable
- `request_to_session_status`
- `session_handoff`
  - `status` — same semantic state as `request_to_session_status`
  - `linked_session_id` — nullable
  - `session_route_href` — nullable; `/api/v1/consultations/{session_id}` when linked
  - `note` — backend-owned operator-readable handoff note
- `allowedActions.canCancel`
- `links.self`
- `links.workbench_detail`
- `meta.snapshot_at`
- `meta.surfaces.consult_request_detail` — `"fresh"` | `"stale"` | `"degraded"` | `"unavailable"`

### Cancel consult request

- `POST /api/v1/consult/requests/{request_id}/cancel`

Required response fields:

- `request_id`
- `status` — must return `"canceled"`
- `canceled_at`
- `linked_session_id`
- `request_to_session_status`
- `allowedActions.canCancel` — must return `false`

## ConsultRequest Object

Canonical lifecycle:

- `created` — request exists, `linked_session_id = null`, `request_to_session_status = "pending_session"`
- `running` — Persona Plane created the consultation session, `linked_session_id != null`, `request_to_session_status = "session_running"`
- `completed` — linked consultation session reached a terminal outcome, `linked_session_id != null`, `request_to_session_status = "session_completed"`
- `canceled` — request was canceled before a session outcome completed; if no session was created then `request_to_session_status = "canceled_before_session"`

Required invariants:

- `request_id` is the canonical identity for all Consultation Workbench modules that need request lineage.
- `linked_session_id` is the only canonical bridge from `ConsultRequest` to `SessionPersona`.
- The BFF must not infer session creation from elapsed time or queue age.
- `allowedActions.canCancel` is the sole truth for rendering the cancel CTA.
- `allowedActions.canCancel` must be `false` when `status` is `completed` or `canceled`.

## Request-to-Session Handoff Semantics

The BFF does not create consultation sessions. Session creation remains Persona Plane responsibility.

The contract boundary is:

1. `POST /api/v1/consult/requests` persists the request and returns `status = "created"`.
2. Persona Plane later materializes the consultation `SessionPersona`.
3. The BFF updates `linked_session_id` and moves the request into `status = "running"` with `request_to_session_status = "session_running"`.
4. When the linked consultation reaches a terminal outcome, the BFF moves the request into `status = "completed"` and returns `request_to_session_status = "session_completed"`.
5. If the request is canceled before completion, the BFF returns `status = "canceled"` and the matching handoff state.

The frontend must never invent request progression or session linkage. It renders the backend-owned `linked_session_id`, `request_to_session_status`, and `session_handoff.note` verbatim.

## Degradation Rules

- When `meta.surfaces.consult_request_list = "degraded"` or `"unavailable"`, the UI must not present "no requests" as authoritative.
- When `meta.surfaces.consult_request_detail = "unavailable"`, suppress detail content and the cancel CTA.
- When either surface is not `"fresh"`, the shared degradation substrate from `PKT-005` must be shown.

## Write Authority

- Request creation: `POST /api/v1/consult/requests`
- Request cancellation: `POST /api/v1/consult/requests/{request_id}/cancel`

The BFF must not expose a write path for session creation, transcript events, committee synthesis, or memo publication in this packet.

## Relationship to Existing Consultation Surfaces

- `GET /api/v1/personas/{persona_id}/consultations` remains the persona-scoped read surface.
- `GET /api/v1/consultations/{session_id}` remains the session detail surface once `linked_session_id` exists.
- `CW-01` adds request identity and lifecycle truth; it does not replace the consultation session contract.

## Example Payload

- `docs/examples/CW-01-consult-request.json`
