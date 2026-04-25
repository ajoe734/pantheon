# PKT-010 Operator Runtime State Board

## Classification

- Workbench: Operator Console
- Screen ID: `screen-operator-runtime-state-board`
- Feature ID: `PKT-010-runtime-state-board`
- Packet status: ready

## User Goal

Give an operator one truthful board for current runtime stage, runtime status, telemetry summary, rollback-history entry points, and freshness so they can inspect runtime state without joining multiple surfaces in the browser.

## Page Sections

- **Board header**: shows the board title, last snapshot time from `meta.snapshot_at`, and the degradation banner when any `meta.surfaces.*` entry is not `ok`.
- **Filter rail**: backend-owned controls for `deployment_stage`, `status`, `sort_by`, and `sort_order`.
- **Runtime roster table**: one row per runtime from `runtimes[]`, showing runtime identity, deployment stage, runtime status, artifact version, and last updated time.
- **Telemetry summary cell**: renders the backend-shaped telemetry block from `telemetry_summary.metrics` and `telemetry_summary.collected_at`.
- **Rollback summary cell**: shows `rollback_summary.count`, the latest rollback snippet when present, and a link target derived from `rollback_summary.href`.
- **Deployment review link**: if `plan_ref` is present, renders the jump-to-review link from `plan_ref.href`.
- **Unavailable board state**: when `meta.surfaces.runtime_state = unavailable`, replaces the table with the explicit unavailable copy.
- **Pagination footer**: renders `page_info.next_page_token` driven pagination only.

## Interaction Rules

- All production data comes from `GET /api/v1/operator/runtime-state`.
- The UI must not issue one request per row to `RT-03`, `RT-04`, or `TL-02` to build or repair the board.
- Filters and sorting are server-backed. The UI may reflect local control state, but the displayed order must come from the BFF response.
- `plan_ref.href` and `rollback_summary.href` are the only navigation targets for downstream owner screens. The UI must not synthesize alternate browser routes.
- When `telemetry_summary` is null, the row renders the telemetry-unavailable state; it does not hide the cell or infer health from another field.
- When `rollback_summary.latest` is null and `count = 0`, the row renders `No recorded rollbacks` rather than a degraded state.
- The board is read-only for this packet. No rollback, pause, or promotion CTA is added here.

## Acceptance

- The runtime roster renders directly from `runtimes[]`.
- Stage, status, telemetry, rollback history, and last-updated values are all backend-shaped; no client-side joins are used.
- The table order follows `meta.sort` and the server-backed query params.
- `runtime_state = unavailable` renders an explicit unavailable state instead of a healthy empty table.
- Any degraded sub-surface triggers the shared degradation banner.
- Links to deployment review and rollback history come only from payload refs.
