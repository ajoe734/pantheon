# PKT-010 Operator Runtime State Board BFF Contract

## Purpose

Provide a single operator-owned runtime roster payload for the Runtime State Board so the UI does not fan out per-runtime requests or join runtime status, telemetry summary, and rollback history in the browser.

## Primary Read Route

- `GET /api/v1/operator/runtime-state`
- Query parameters:
  - `deployment_stage` (comma-separated: `paper`, `canary`, `live`, `frozen`)
  - `status` (comma-separated runtime status values such as `idle`, `running`, `paused`, `degraded`)
  - `sort_by` (`last_updated_at` | `runtime_id` | `deployment_stage` | `status`)
  - `sort_order` (`asc` | `desc`)
  - `page_token`
  - `page_size`

Required response fields:

- `runtimes[]`
  - `runtime_id`
  - `runtime_binding_id`
  - `deployment_stage`
  - `status`
  - `capital_pool_id` (nullable)
  - `plan_ref`
    - `plan_id`
    - `href`
  - `artifact_ref`
    - `artifact_id`
    - `artifact_version`
  - `telemetry_summary` (nullable)
    - `window`
    - `collected_at`
    - `metrics.pnl`
    - `metrics.drawdown`
    - `metrics.sharpe_ratio`
    - `metrics.fill_rate`
    - `metrics.avg_slippage_bps`
    - `metrics.total_trades`
  - `rollback_summary`
    - `count`
    - `latest` (nullable)
      - `rollback_id`
      - `action_type`
      - `status`
      - `from_version`
      - `to_version`
      - `initiated_at`
      - `completed_at`
    - `href`
  - `last_updated_at` (nullable RFC3339)
- `page_info.next_page_token`
- `meta.snapshot_at`
- `meta.total`
- `meta.sort.sort_by`
- `meta.sort.sort_order`
- `meta.surfaces.runtime_state`
- `meta.surfaces.runtime_roster`
- `meta.surfaces.telemetry_summary`
- `meta.surfaces.rollback_history`

## Degraded-State Rules

- When `meta.surfaces.runtime_state = unavailable`, return `runtimes: []` and preserve the surface-level unavailable message. The UI must render an explicit unavailable state, not an empty healthy table.
- When `meta.surfaces.runtime_roster = degraded`, the roster may still render, but the UI must show the degradation banner and keep the board read-only.
- When `meta.surfaces.telemetry_summary = degraded` or `unavailable`, any row with `telemetry_summary = null` renders the backend-supplied unavailable copy. The UI must not backfill telemetry from another route.
- When `meta.surfaces.rollback_history = degraded` or `unavailable`, the rollback history link remains visible only when `rollback_summary.href` is present; the UI must not infer rollback freshness from `count = 0`.

## Design Rules

- The Runtime State Board reads from `GET /api/v1/operator/runtime-state` only. The UI must not stitch rows together from `RT-03`, `RT-04`, and `TL-02` in the browser.
- Backend-owned filter and sort semantics live on this route. The UI may pass `deployment_stage`, `status`, `sort_by`, `sort_order`, `page_token`, and `page_size`, but must not reorder rows locally as a substitute for the route contract.
- Cross-links come from the payload only:
  - `plan_ref.href` points to the deployment review owner screen
  - `rollback_summary.href` points to rollback history
- This packet is read-only. It does not introduce rollback, pause, or promotion authority.
- If a required row field or `meta.surfaces.*` entry is missing, the frontend must emit a `bff-gap` handoff instead of inventing shadow state.
- Inherits degradation semantics from `PKT-005 Degradation Banner`.

## Example Payload

- `docs/examples/PKT-010-runtime-state-board.json`
