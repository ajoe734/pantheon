# PKT-003 Evolution Center

## Classification

- Workbench: Evolution Workbench
- Screen ID: `screen-evolution-center`
- Feature ID: `PKT-003-evolution-center`
- Packet status: ready

## User Goal

Give an operator a consolidated view of evolution decisions, freeze orders, and rollback records so they can understand the current evolution state of the system without navigating individual incident or deployment records.

## Page Sections

- **Evolution Decisions panel**: list of `evolution-decisions` filtered by `action_type`, `risk_level`, or `status`. Each row shows `id`, `action_type`, `risk_level`, `status`, `incident_ref`, and `artifact_id`. Source: `GET /api/v1/evolution-decisions`.
- **Evolution Decision Detail drawer**: slides open on row selection. Shows full decision record including all fields from EV-02. Source: `GET /api/v1/evolution-decisions/{decision_id}`.
- **Freeze Orders panel**: list of active and historical freeze orders. Each row shows `freeze_order_id`, `status`, `scope`, and `issued_at`. Source: `GET /api/v1/freeze-orders` (no status filter — active and lifted orders are both included).
- **Rollbacks panel**: list of rollback records. Each row shows `rollback_id`, `action_type`, `runtime_id`, and `executed_at`. Source: `GET /api/v1/rollbacks`.
- **Degradation banner**: when any panel's data is stale or the BFF state is not `fresh`, a non-dismissable banner explains which surface is affected.
- **Loading, empty, and error states**: explicit and visually distinct with no mock fallback.

## Interaction Rules

- All production data comes from Pantheon BFF routes only.
- Filter parameters (`action_type`, `risk_level`, `status`, `scope`) are passed as query parameters; the UI does not filter client-side.
- `time_range` filter for `GET /api/v1/rollbacks` is accepted by the BFF but not applied in v1 store — do not expose as a UI control until confirmed live.
- No write actions on this screen — evolution decision mutations are gated on the Mutation Review screen (`PKT-003-mutation-review`), which is blocked pending EVO-004.
- If a required field is absent, emit a `bff-gap` handoff.

## Acceptance

- Evolution Decisions panel renders from real BFF data with no mock rows.
- Decision Detail drawer renders all EV-02 fields on row selection.
- Freeze Orders and Rollbacks panels render and handle empty states explicitly.
- Degradation banner renders when `BFF_READ_SURFACE_STATE != fresh`.
- Loading, empty, and error states are explicit and visually distinct.
- Front-end emits a `bff-gap` handoff if any expected response field is absent.
