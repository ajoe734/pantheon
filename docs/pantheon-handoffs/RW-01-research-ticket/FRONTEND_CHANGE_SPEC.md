# RW-01 Research Ticket — Frontend Change Spec

## Feature

- Feature ID: `RW-01-research-ticket`
- Screen ID: `screen-research-ticket`
- Workbench: Research Workbench
- Packet status: contract-published — UI implementation must not start until the BFF routes are live
- Task: `RW-01-FOUNDATION-001`

## Summary

Build the Research Workbench ticket surfaces inside `front-ai-trading-system`. This slice includes the ticket composer, ticket list, ticket detail page, lifecycle state rail, and edit/close/archive CTAs. All ticket identity, lifecycle, state transitions, and CTA authority must come from the Pantheon BFF.

## Files to Create or Modify

```
src/pages/research/ResearchTicketList.tsx      — new ticket composer + list page
src/pages/research/ResearchTicketDetail.tsx    — new ticket detail page
src/pages/research/types.ts                    — add research-ticket types
src/lib/bffClient.ts                           — add RW-01 ticket calls
```

## Readiness Gate

Do not open the production page until Pantheon confirms these routes are live and returning the published field shape:

- `POST /api/v1/research/tickets`
- `GET /api/v1/research/tickets`
- `GET /api/v1/research/tickets/{ticket_id}`
- `PATCH /api/v1/research/tickets/{ticket_id}`

Until then, render a blocked placeholder. No invented ticket objects.

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` in component files.

### Create research ticket

```http
POST /api/v1/research/tickets
```

Body fields:

- `title`
- `description`
- `priority`
- `owner`

### List research tickets

```http
GET /api/v1/research/tickets
```

Supported query params:

- `status`
- `owner`
- `page_token`
- `page_size`

### Get ticket detail

```http
GET /api/v1/research/tickets/{ticket_id}
```

Required detail-only fields:

- `lifecycle_history[]`
- `linked_experiments[]`
- `linked_artifacts[]`
- `allowedActions.canEdit`
- `allowedActions.canClose`
- `allowedActions.canArchive`

### Patch ticket

```http
PATCH /api/v1/research/tickets/{ticket_id}
```

Accepted fields: `status`, `title`, `description`, `priority`, `owner`.

## Component Rules

### `ResearchTicketList.tsx`

- Hosts both the ticket composer and the ticket list.
- Composer fields must exactly match the published create contract.
- List rows must come from the BFF list response only.
- Filter state may be local UI state, but filter vocabulary must match backend query params exactly.
- If `meta.surfaces.ticket_list` is `degraded` or `unavailable`, render the shared degradation banner and do not present an empty list as authoritative.

### `ResearchTicketDetail.tsx`

- Reads `ticket_id` from `/research/tickets/:ticket_id`.
- Renders the full ticket object, lifecycle state rail, linked entity refs, and action CTAs.
- Lifecycle history is rendered as a step-by-step state timeline from `lifecycle_history[]`; do not construct a timeline from `status` alone.
- If `linked_experiments[]` is non-empty, render navigation links to experiment detail routes.
- If `linked_artifacts[]` is non-empty, render navigation links to artifact detail routes.
- Edit fields are active only when `allowedActions.canEdit === true`.
- Close CTA is visible only when `allowedActions.canClose === true`.
- Archive CTA is visible only when `allowedActions.canArchive === true`.
- After any patch submission, re-fetch the detail route. No optimistic state mutation.

## Constraints

- Use the existing BFF client only.
- Do not add raw network calls in components.
- Do not derive ticket lifecycle, state availability, or CTA authority from client-side logic.
- Do not infer action availability from `status`; use `allowedActions.*`.
- Do not start production UI until Pantheon confirms the routes are live.
- If any required field is missing, emit a `bff-gap` handoff instead of mocking.

## Degradation Handling

| State | Handling |
|---|---|
| `meta.surfaces.ticket_list = "stale"` | render non-dismissable staleness banner; list remains visible |
| `meta.surfaces.ticket_list = "degraded"` | render degradation banner; suppress authoritative empty-state claims |
| `meta.surfaces.ticket_list = "unavailable"` | suppress list rendering and show unavailable notice |
| `meta.surfaces.ticket_detail = "stale"` | render staleness banner on detail page |
| `meta.surfaces.ticket_detail = "degraded"` | render degradation banner and suppress all action CTAs unless authority signals are still explicitly present |
| `meta.surfaces.ticket_detail = "unavailable"` | suppress detail rendering and all action CTAs |

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/RW-01-research-ticket-ui-done.yaml` using `.coordination/requests/RW-01-research-ticket-ui-done.example.yaml` as the template.

## References

- Screen spec: `docs/screens/RW-01-research-ticket.md`
- BFF contract: `docs/bff/RW-01-research-ticket.md`
- Example payload: `docs/examples/RW-01-research-ticket.json`
- Contract-ready: `.coordination/responses/RW-01-research-ticket-contract-ready.yaml`
- Lovable UI task: `.coordination/responses/RW-01-research-ticket-lovable-ui-task.yaml`
- Packet family: `docs/pantheon-handoffs/RW-005-research-workbench/PACKET_FAMILY.md`
