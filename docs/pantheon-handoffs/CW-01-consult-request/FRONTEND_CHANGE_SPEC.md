# CW-01 Consult Request — Frontend Change Spec

## Feature

- Feature ID: `CW-01-consult-request`
- Screen ID: `screen-consult-request`
- Workbench: Consultation Workbench
- Packet status: contract-published — UI implementation must not start until the BFF routes are live
- Task: `CW-01-FOUNDATION-001`

## Summary

Build the Consultation Workbench request surfaces inside `front-ai-trading-system`. This slice includes the request composer, request list, request detail page, request-to-session status rail, and cancel CTA. All request identity, lifecycle, session linkage, and CTA authority must come from the Pantheon BFF.

## Files to Create or Modify

```
src/pages/consultation/ConsultRequestList.tsx      — new request composer + list page
src/pages/consultation/ConsultRequestDetail.tsx    — new request detail page
src/pages/consultation/types.ts                    — add consult-request types
src/lib/bffClient.ts                               — add CW-01 request calls
```

## Readiness Gate

Do not open the production page until Pantheon confirms these routes are live and returning the published field shape:

- `POST /api/v1/consult/requests`
- `GET /api/v1/consult/requests`
- `GET /api/v1/consult/requests/{request_id}`
- `POST /api/v1/consult/requests/{request_id}/cancel`

Until then, render a blocked placeholder. No invented request objects.

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` in component files.

### Create consult request

```http
POST /api/v1/consult/requests
```

Body fields:

- `from_persona_id`
- `target_type`
- `target_ref`
- `task`
- `context_refs[]`
- `priority`
- `consultation_type`

### List consult requests

```http
GET /api/v1/consult/requests
```

Supported query params:

- `status`
- `target_type`
- `consultation_type`
- `page_token`
- `page_size`

### Get request detail

```http
GET /api/v1/consult/requests/{request_id}
```

Required detail-only fields:

- `linked_session_id`
- `request_to_session_status`
- `session_handoff`
- `allowedActions.canCancel`

### Cancel request

```http
POST /api/v1/consult/requests/{request_id}/cancel
```

## Component Rules

### `ConsultRequestList.tsx`

- Hosts both the request composer and the request list.
- Composer fields must exactly match the published create contract.
- List rows must come from the BFF list response only.
- Filter state may be local UI state, but filter vocabulary must match backend query params exactly.
- If `meta.surfaces.consult_request_list` is `degraded` or `unavailable`, render the shared degradation banner and do not present an empty list as authoritative.

### `ConsultRequestDetail.tsx`

- Reads `request_id` from `/consultation/requests/:request_id`.
- Renders the full request object, the request-to-session rail, and the cancel CTA.
- If `session_handoff.session_route_href` exists, render a link to that route; otherwise show the backend note only.
- The cancel CTA is visible only when `allowedActions.canCancel === true`.
- After cancel submission, re-fetch the detail route. No optimistic state mutation.

## Constraints

- Use the existing BFF client only.
- Do not add raw network calls in components.
- Do not synthesize request lifecycle or session linkage from persona consultation endpoints.
- Do not infer cancel availability from `status`; use `allowedActions.canCancel`.
- Do not start production UI until Pantheon confirms the routes are live.
- If any required field is missing, emit a `bff-gap` handoff instead of mocking.

## Degradation Handling

| State | Handling |
|---|---|
| `meta.surfaces.consult_request_list = "stale"` | render non-dismissable staleness banner; list remains visible |
| `meta.surfaces.consult_request_list = "degraded"` | render degradation banner; suppress authoritative empty-state claims |
| `meta.surfaces.consult_request_list = "unavailable"` | suppress list rendering and show unavailable notice |
| `meta.surfaces.consult_request_detail = "stale"` | render staleness banner on detail page |
| `meta.surfaces.consult_request_detail = "degraded"` | render degradation banner and suppress cancel CTA unless the authority signal is still explicitly present |
| `meta.surfaces.consult_request_detail = "unavailable"` | suppress detail rendering and cancel CTA |

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/CW-01-consult-request-ui-done.yaml` using `.coordination/requests/CW-01-consult-request-ui-done.example.yaml` as the template.

## References

- Screen spec: `docs/screens/CW-01-consult-request.md`
- BFF contract: `docs/bff/CW-01-consult-request.md`
- Example payload: `docs/examples/CW-01-consult-request.json`
- Contract-ready: `.coordination/responses/CW-01-consult-request-contract-ready.yaml`
- Lovable UI task: `.coordination/responses/CW-01-consult-request-lovable-ui-task.yaml`
- BFF-gap template: `.coordination/requests/CW-01-consult-request-bff-gap.example.yaml`
- UI-done template: `.coordination/requests/CW-01-consult-request-ui-done.example.yaml`
- Packet family: `docs/pantheon-handoffs/CW-008-consultation-workbench/PACKET_FAMILY.md`
