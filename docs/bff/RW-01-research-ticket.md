# RW-01 Research Ticket BFF Contract

## Status

**Contract published** — the ticket identity, lifecycle semantics, and create/list/detail/patch route shapes are now the definitive implementation target for the Pantheon BFF. UI work must not start until Pantheon confirms the routes are live and returning this field shape.

Task: `RW-01-FOUNDATION-001`

## Purpose

Provide one canonical ticket surface for the Research Workbench so researchers can create, list, inspect, and transition research tickets without inventing ticket lifecycle, state machine progression, or action authority in the browser.

## Routes

### Create research ticket

- `POST /api/v1/research/tickets`

Required request body:

- `title` — short label for the research question or investigation
- `description` — full statement of the research goal or hypothesis
- `priority` — `"low"` | `"normal"` | `"high"` | `"critical"`
- `owner` — persona identity assigned as the responsible researcher

Required response fields:

- `ticket_id`
- `status` — must return `"open"` for a newly created ticket
- `created_at`
- `allowedActions.canEdit`
- `allowedActions.canClose`
- `allowedActions.canArchive`

### List research tickets

- `GET /api/v1/research/tickets`

Supported query params:

- `status` — filter by lifecycle state
- `owner` — filter by responsible researcher
- `page_token`
- `page_size`

Required response fields:

- `data[]`
  - `ticket_id`
  - `title`
  - `status`
  - `priority`
  - `owner`
  - `created_at`
  - `updated_at`
  - `allowedActions.canEdit`
  - `allowedActions.canClose`
  - `allowedActions.canArchive`
- `page_info.next_page_token`
- `page_info.total`
- `meta.snapshot_at`
- `meta.surfaces.ticket_list` — `"fresh"` | `"stale"` | `"degraded"` | `"unavailable"`

### Get research ticket detail

- `GET /api/v1/research/tickets/{ticket_id}`

Required response fields:

- `ticket_id`
- `title`
- `description`
- `status`
- `priority`
- `owner`
- `created_at`
- `updated_at`
- `closed_at` — nullable
- `archived_at` — nullable
- `lifecycle_history[]`
  - `from_status`
  - `to_status`
  - `transitioned_at`
  - `transitioned_by`
- `linked_experiments[]` — array of `experiment_id` refs; empty if none
- `linked_artifacts[]` — array of `artifact_id` refs; empty if none
- `allowedActions.canEdit`
- `allowedActions.canClose`
- `allowedActions.canArchive`
- `links.self`
- `links.workbench_detail`
- `meta.snapshot_at`
- `meta.surfaces.ticket_detail` — `"fresh"` | `"stale"` | `"degraded"` | `"unavailable"`

### Patch research ticket

- `PATCH /api/v1/research/tickets/{ticket_id}`

Accepted patch fields:

- `status` — lifecycle transition; the BFF must validate the transition against the state machine and reject invalid hops
- `title` — editable when `allowedActions.canEdit` is `true`
- `description` — editable when `allowedActions.canEdit` is `true`
- `priority` — editable when `allowedActions.canEdit` is `true`
- `owner` — editable when `allowedActions.canEdit` is `true`

Required response fields:

- `ticket_id`
- `status`
- `updated_at`
- `allowedActions.canEdit`
- `allowedActions.canClose`
- `allowedActions.canArchive`

## ResearchTicket Object

Canonical lifecycle:

- `open` — ticket created and active; all edit actions available when `canEdit` is `true`
- `in_progress` — active research work is underway; linked experiments may be present
- `closed` — research work concluded; ticket transitions to terminal read state
- `archived` — closed ticket moved to long-term storage; no further write actions allowed

Required invariants:

- `ticket_id` is the canonical identity for all Research Workbench modules that need corpus identity, lineage, and history.
- Lifecycle transitions are governed by `allowedActions`; the frontend must never derive transition availability from status alone.
- `allowedActions.canClose` must be `false` when `status` is `closed` or `archived`.
- `allowedActions.canArchive` must be `false` when `status` is `open` or `in_progress`.
- `allowedActions.canEdit` must be `false` when `status` is `archived`.
- The BFF must reject status transitions that skip states (e.g., `open → archived` without passing through `closed` is forbidden).
- `linked_experiments[]` and `linked_artifacts[]` are read-only projections managed by the BFF; the frontend must not construct or infer these lists.

## Degradation Rules

- When `meta.surfaces.ticket_list = "degraded"` or `"unavailable"`, the UI must not present an empty list as authoritative.
- When `meta.surfaces.ticket_detail = "unavailable"`, suppress detail content and all action CTAs.
- When either surface is not `"fresh"`, the shared degradation substrate from `PKT-005` must be shown.

## Write Authority

- Ticket creation: `POST /api/v1/research/tickets`
- Ticket update and lifecycle transition: `PATCH /api/v1/research/tickets/{ticket_id}`

The BFF must not expose a write path for experiment creation, artifact attachment, or search indexing in this packet. Those remain the responsibility of downstream RW-02 through RW-05 modules.

## Relationship to Downstream Research Modules

- `GET /api/v1/research/tickets/{ticket_id}` provides the `ticket_id` anchor for all corpus identity queries in RW-02 Search, RW-03 Analyze, and RW-04 Experiment Launch.
- `linked_experiments[]` and `linked_artifacts[]` are populated by the BFF as experiments and artifacts reference this ticket; the frontend must render them as read-only lineage, not editable relationships.

## Example Payload

- `docs/examples/RW-01-research-ticket.json`
