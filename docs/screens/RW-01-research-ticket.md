# RW-01 Research Ticket

## Classification

- Workbench: Research Workbench
- Screen ID: `screen-research-ticket`
- Feature ID: `RW-01-research-ticket`
- Packet status: **contract-published** — ticket identity, lifecycle, and create/list/detail/patch semantics are defined; BFF implementation is the remaining gate before UI work starts
- Task: `RW-01-FOUNDATION-001`

## Contract Note

The ticket contract and lifecycle state machine are now published. UI implementation must not start until Pantheon confirms that the create, list, detail, and patch routes are live and returning the published field shape.

The UI must not infer ticket lifecycle, state transitions, or CTA availability from client-side logic, polling heuristics, or local state.

## User Goal

Let a researcher create a research ticket, browse the ticket list with status and owner filters, inspect the full ticket context and lifecycle history, and transition lifecycle state only when the backend-shaped authority signal allows it.

## Routes

Primary routes:

- `/research/tickets`
- `/research/tickets/:ticket_id`

## Readiness Gate

Do not open the production page until Pantheon confirms:

1. `POST /api/v1/research/tickets` is live with the published request body and response shape.
2. `GET /api/v1/research/tickets` is live with `status`, `owner`, pagination, and `meta.surfaces.ticket_list`.
3. `GET /api/v1/research/tickets/{ticket_id}` is live with `lifecycle_history[]`, `linked_experiments[]`, `linked_artifacts[]`, and `allowedActions`.
4. `PATCH /api/v1/research/tickets/{ticket_id}` is live and validates state machine transitions.

Until those gates are met, render a blocked placeholder for both routes. No invented ticket objects.

## Page Sections

### 1. Ticket Composer

- Lives on `/research/tickets`.
- Fields come from the published create contract only:
  - `title`
  - `description`
  - `priority`
  - `owner`
- Submission target: `POST /api/v1/research/tickets`
- The owner selector must use backend-provided persona identities. Do not hardcode persona labels that are not backend-owned.

### 2. Ticket List

- Also lives on `/research/tickets`.
- Renders rows from `GET /api/v1/research/tickets`.
- Filters:
  - `status`
  - `owner`
- Each row shows:
  - `ticket_id`
  - `title`
  - `status`
  - `priority`
  - `owner`
  - `created_at`
- Row click navigates to `/research/tickets/:ticket_id`.

### 3. Ticket Detail

- Lives on `/research/tickets/:ticket_id`.
- Displays the full ticket object from `GET /api/v1/research/tickets/{ticket_id}`.
- Required detail fields:
  - full `title` and `description`
  - `status` with lifecycle state badge
  - `priority` and `owner`
  - `lifecycle_history[]` rendered as a state-progression timeline
  - `linked_experiments[]` rendered as navigation links to experiment detail
  - `linked_artifacts[]` rendered as navigation links to artifact detail

### 4. Lifecycle State Rail

- Backend-owned state rail that makes lifecycle progression explicit.
- Uses `lifecycle_history[]` and current `status`.
- Must distinguish: `open` → `in_progress` → `closed` → `archived`.
- The frontend must not derive state transitions or step labels from `status` alone; render the history provided by the BFF.

### 5. Edit and Lifecycle Action CTAs

- Edit fields (`title`, `description`, `priority`, `owner`) are visible and active only when `allowedActions.canEdit === true`.
- Close CTA is visible only when `allowedActions.canClose === true`.
- Archive CTA is visible only when `allowedActions.canArchive === true`.
- All actions submit to `PATCH /api/v1/research/tickets/{ticket_id}`.
- After submission, re-read the detail route. Do not optimistically update lifecycle state.

## Degradation Handling

| Surface state | Required behavior |
|---|---|
| `meta.surfaces.ticket_list = "fresh"` | normal list and composer rendering |
| `meta.surfaces.ticket_list = "stale"` | non-dismissable staleness banner; list remains visible |
| `meta.surfaces.ticket_list = "degraded"` | show degradation banner; do not present an empty list as authoritative |
| `meta.surfaces.ticket_list = "unavailable"` | suppress list rendering; keep only degraded-state notice |
| `meta.surfaces.ticket_detail = "stale"` | show staleness banner on detail page |
| `meta.surfaces.ticket_detail = "degraded"` | show degradation banner and suppress action CTAs unless `allowedActions` is still explicitly present |
| `meta.surfaces.ticket_detail = "unavailable"` | replace detail content with unavailable notice and suppress all action CTAs |

## Constraints

- Use the Pantheon BFF only. No mock ticket objects.
- Do not derive ticket lifecycle, state transitions, or action authority client-side.
- If any required field is missing, emit a `bff-gap` handoff instead of rendering with invented state.

## Acceptance

- Ticket composer submits only the published create shape.
- Ticket list renders from `GET /api/v1/research/tickets` with backend-owned filters and pagination.
- Ticket detail renders the canonical ticket object with lifecycle history and linked entity refs.
- Edit, close, and archive CTAs are visible only when the respective `allowedActions` signal is `true`.
- Degradation behavior follows the published `meta.surfaces.*` rules.

## References

- BFF contract: `docs/bff/RW-01-research-ticket.md`
- Example payload: `docs/examples/RW-01-research-ticket.json`
- Frontend change spec: `docs/pantheon-handoffs/RW-01-research-ticket/FRONTEND_CHANGE_SPEC.md`
- Packet family: `docs/pantheon-handoffs/RW-005-research-workbench/PACKET_FAMILY.md`
