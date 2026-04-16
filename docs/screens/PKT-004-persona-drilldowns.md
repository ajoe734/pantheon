# PKT-004 Module A — Persona Drilldowns

## Classification

- Workbench: Persona Workbench
- Module: `Module A — Persona Drilldowns`
- Feature ID: `PKT-004-persona-drilldowns`
- Packet status: ready (catalog packetization; standalone persona list/detail IA deferred to Wave 2)

## User Goal

Allow an operator to browse the full persona catalog, inspect individual persona detail with bindings, review session lists and detail, view teaching history, and inspect capability snapshots — each as a separately addressable drilldown surface.

## Surface Inventory

| Surface | Screen ID | Endpoint | User action |
|---|---|---|---|
| `PS-01` Persona Catalog | `screen-persona-catalog` | `GET /api/v1/personas` | Browse all personas with lifecycle state, mandate, and strategy family |
| `PS-02` Persona Detail | `screen-persona-detail` | `GET /api/v1/personas/{persona_id}` | Inspect a single persona with embedded bindings |
| `PS-03` Session List | `screen-persona-sessions` | `GET /api/v1/personas/{persona_id}/sessions` | List sessions for a persona with status, tools, and pool scope |
| `PS-04` Session Detail | `screen-session-detail` | `GET /api/v1/sessions/{session_id}` | Inspect a single session with capability snapshot |
| `PS-05` Teaching History | `screen-persona-teaching` | `GET /api/v1/personas/{persona_id}/teaching` | List teaching sessions with outcomes and artifacts |
| `PS-06` Capability Snapshot | `screen-persona-capabilities` | `GET /api/v1/personas/{persona_id}/capabilities` | View effective tools, skills, workflows, and restrictions |

## Page Sections Per Surface

### PS-01 Persona Catalog

- **Persona list**: paginated list rows showing `id`, `name`, `lifecycle_state`, `mandate`, `strategy_family`, `last_active_at`.
- **Filter rail**: filter by `lifecycle_state`. Filters passed as query parameters — no client-side filtering.
- **Row actions**: clicking a row navigates to `PS-02 Persona Detail` or to the `PM-01 Persona Management` composed screen.
- **Degradation banner**: when `meta.staleness` is non-null, show non-dismissable staleness notice.
- **Loading, empty, and error states**: explicit and visually distinct.

### PS-02 Persona Detail

- **Persona identity block**: `id`, `name`, `lifecycle_state`, `mandate`, `strategy_family`, `created_at`, `last_active_at`.
- **Bindings summary**: list of embedded bindings with `capital_pool_id`, `validity`, `status`, `allowed_deployment_scope`.
- **Navigation links**: links to Session List (`PS-03`), Teaching History (`PS-05`), Capability Snapshot (`PS-06`).

### PS-03 Session List

- **Session list**: rows showing `id`, `status`, `started_at`, `last_heartbeat_at`, `tools_enabled`, `pool_scope`, `deployment_stage`.
- Clicking a row navigates to `PS-04 Session Detail`.

### PS-04 Session Detail

- **Session header**: `id`, `persona_id`, `status`, `deployment_stage`, `capital_pool_id`, `started_at`, `last_heartbeat_at`.
- **Tools rail**: `tools_enabled[]`.
- **Capability snapshot inline**: `effective_tools`, `effective_skills`, `effective_workflows`, `restrictions`, `generated_at`.

### PS-05 Teaching History

- **Teaching session list**: rows showing `id`, `status`, `started_at`, `completed_at`, `topic`, `operator_id`.
- **Outcomes drawer**: `outcomes[]` and `session_artifacts[]` visible on row expand.

### PS-06 Capability Snapshot

- **Snapshot header**: `snapshot_id`, `generated_at`, `source_refs[]`.
- **Effective tools**: `effective_tools[]`.
- **Effective skills**: `effective_skills[]`.
- **Effective workflows**: `effective_workflows[]`.
- **Restrictions**: `restrictions[]`.

## Wave 2 Deferred Items

The standalone Persona Workbench list/detail IA shell (full navigation model, breadcrumbs, workbench sidebar, and URL routing between PS-01 through PS-06) is Wave 2 work. The individual endpoint-level surfaces defined here are packet-ready now, but a full standalone Persona Workbench IA requires additional packet language not included in this Wave 1 packet.

## Non-Blocking BFF Caveats

- `viewer` role tokens are rejected on persona endpoints: requires `operator`, `approver`, `admin`, or `reviewer`.
- `meta.staleness` on list surfaces may be non-null when the backing store has not recently refreshed.

## Acceptance

- All six PS surfaces render with real BFF data and no mock rows.
- Navigation between surfaces uses the canonical endpoint IDs above.
- Filters are passed as query parameters to the BFF — no client-side filter logic.
- Loading, empty, and error states are explicit and visually distinct.
