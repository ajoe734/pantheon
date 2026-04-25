# PKT-012 Operator Alerts Rail BFF Contract

## Purpose

Provide one operator-owned alert feed so the UI does not merge incidents, governance queues, kill-switch state, and runtime anomalies in the browser.

## Primary Read Route

- `GET /api/v1/operator/alerts`

Required response fields:

- `alerts[]`
  - `alert_id`
  - `severity` (`critical` | `high` | `medium` | `low`)
  - `category` (`incident` | `governance` | `kill_switch` | `runtime`)
  - `raised_at` (RFC3339)
  - `summary`
  - `target_ref`
    - `surface_id`
    - `label`
    - `href`
    - `target_id` (nullable)
- `summary`
  - `total_active`
  - `highest_severity` (nullable)
  - `by_severity`
  - `by_category`
- `meta.snapshot_at`
- `meta.acknowledgement_supported`
- `meta.surfaces.alerts`
- `meta.surfaces.incident_feed`
- `meta.surfaces.review_queue`
- `meta.surfaces.approval_queue`
- `meta.surfaces.kill_switch`
- `meta.surfaces.runtime_roster`
- `meta.surfaces.telemetry_summary`

## Degraded-State Rules

- When `meta.surfaces.alerts = unavailable`, return `alerts: []` and preserve the unavailable message. The UI must render an unavailable state, not a healthy empty rail.
- When a contributing surface is degraded or unavailable, the rail may still render any returned alerts, but the page must also render the shared degradation banner.
- `meta.acknowledgement_supported = false` is explicit. This packet is read-only and must not render an acknowledgement CTA.
- `target_ref` is backend-owned. The UI must not invent browser routes, infer alternate owners, or remap categories.

## Design Rules

- The Alerts Rail reads from `GET /api/v1/operator/alerts` only.
- Severity, category, ordering, and stable `alert_id` values are backend-owned.
- Runtime anomalies come from the backend-owned runtime roster and telemetry summaries. The UI must not inspect raw runtime or telemetry primitives to create or suppress alerts.
- Governance alerts stay linked to existing owners:
  - review queue items point to `PKT-001`
  - approval queue items point to `GV-02`
- Kill-switch and safe-mode alerts link to `OC-03`.
- Runtime anomaly alerts link to `OC-04`.
- Incident alerts link to `PKT-002`.
- If any required field or `meta.surfaces.*` entry is missing, the frontend must emit a `bff-gap` handoff instead of inventing substitute logic.

## Example Payload

- `docs/examples/PKT-012-alerts-rail.json`
