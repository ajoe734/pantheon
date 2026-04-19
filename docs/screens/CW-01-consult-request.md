# CW-01 Consult Request

## Classification

- Workbench: Consultation Workbench
- Screen ID: `screen-consult-request`
- Feature ID: `CW-01-consult-request`
- Packet status: **contract-published** — request list, detail, create, and cancel semantics are defined; BFF implementation is the remaining gate before UI work starts
- Task: `CW-01-FOUNDATION-001`

## Contract Note

The request contract and request-to-session handoff semantics are now published. UI implementation must not start until Pantheon confirms that the create, list, detail, and cancel routes are live and returning the published field shape.

The UI must not infer request lifecycle or session creation from timers, polling heuristics, or persona-side consultation endpoints.

## User Goal

Let an operator open a consultation request, monitor whether it has handed off into a real consultation session, inspect the full request context, and cancel only when the backend-shaped authority signal allows it.

## Routes

Primary routes:

- `/consultation/requests`
- `/consultation/requests/:request_id`

## Readiness Gate

Do not open the production page until Pantheon confirms:

1. `POST /api/v1/consult/requests` is live with the published request body and response shape.
2. `GET /api/v1/consult/requests` is live with filters, pagination, and `meta.surfaces.consult_request_list`.
3. `GET /api/v1/consult/requests/{request_id}` is live with `linked_session_id`, `request_to_session_status`, and `session_handoff`.
4. `POST /api/v1/consult/requests/{request_id}/cancel` is live and gated by `allowedActions.canCancel`.

Until those gates are met, render a blocked placeholder for both routes. No invented request objects.

## Page Sections

### 1. Request Composer

- Lives on `/consultation/requests`.
- Fields come from the published create contract only:
  - `from_persona_id`
  - `target_type`
  - `target_ref`
  - `task`
  - `context_refs[]`
  - `priority`
  - `consultation_type`
- Submission target: `POST /api/v1/consult/requests`
- The target selector must use backend-provided options or canonical identities already available in the app. Do not hardcode committee or persona labels that are not backend-owned.

### 2. Request List

- Also lives on `/consultation/requests`.
- Renders rows from `GET /api/v1/consult/requests`.
- Filters:
  - `status`
  - `target_type`
  - `consultation_type`
- Each row shows:
  - `request_id`
  - `status`
  - `target_type`
  - `target_ref`
  - `consultation_type`
  - `priority`
  - `created_at`
  - `request_to_session_status`
- Row click navigates to `/consultation/requests/:request_id`.

### 3. Request Detail

- Lives on `/consultation/requests/:request_id`.
- Displays the full request object from `GET /api/v1/consult/requests/{request_id}`.
- Required detail fields:
  - request identity and lifecycle
  - full `task`
  - `context_refs[]`
  - `linked_session_id`
  - `session_handoff.status`
  - `session_handoff.note`
- If `session_handoff.session_route_href` is present, render a navigation link to the consultation session surface.

### 4. Request-to-Session Status Rail

- Backend-owned state rail that makes the handoff explicit.
- Uses `request_to_session_status` and `session_handoff`.
- Must distinguish:
  - pending session creation
  - live linked session
  - completed linked session
  - canceled before session
- The frontend must not derive these states from `status` plus null checks alone when the explicit backend field is available.

### 5. Cancel CTA

- Visible only when `allowedActions.canCancel === true`.
- Submission target: `POST /api/v1/consult/requests/{request_id}/cancel`
- After submission, re-read the detail route. Do not optimistically mark the request canceled.

## Degradation Handling

| Surface state | Required behavior |
|---|---|
| `meta.surfaces.consult_request_list = "fresh"` | normal list and composer rendering |
| `meta.surfaces.consult_request_list = "stale"` | non-dismissable staleness banner; list remains visible |
| `meta.surfaces.consult_request_list = "degraded"` | show degradation banner; do not present an empty list as authoritative |
| `meta.surfaces.consult_request_list = "unavailable"` | suppress list rendering; keep only degraded-state notice |
| `meta.surfaces.consult_request_detail = "stale"` | show staleness banner on detail page |
| `meta.surfaces.consult_request_detail = "degraded"` | show degradation banner and suppress cancel CTA unless `allowedActions` is still explicitly present |
| `meta.surfaces.consult_request_detail = "unavailable"` | replace detail content with unavailable notice and suppress cancel CTA |

## Constraints

- Use the Pantheon BFF only. No mock request objects.
- Do not derive request lifecycle, session linkage, or cancel authority client-side.
- Do not reuse `GET /api/v1/personas/{persona_id}/consultations` as a substitute for `CW-01` request identity.
- If any required field is missing, emit a `bff-gap` handoff instead of rendering with invented state.

## Acceptance

- Request composer submits only the published create shape.
- Request list renders from `GET /api/v1/consult/requests` with backend-owned filters and pagination.
- Request detail renders the canonical request object and request-to-session handoff rail.
- Cancel CTA is visible only when `allowedActions.canCancel` is true.
- Linked session navigation appears only when the BFF provides `session_handoff.session_route_href`.
- Degradation behavior follows the published `meta.surfaces.*` rules.

## References

- BFF contract: `docs/bff/CW-01-consult-request.md`
- Example payload: `docs/examples/CW-01-consult-request.json`
- Frontend change spec: `docs/pantheon-handoffs/CW-01-consult-request/FRONTEND_CHANGE_SPEC.md`
- Packet family: `docs/pantheon-handoffs/CW-008-consultation-workbench/PACKET_FAMILY.md`
- Consultation surface baseline: `services/control-plane/bff/CONSULTATION_SURFACE_CONTRACT.md`
