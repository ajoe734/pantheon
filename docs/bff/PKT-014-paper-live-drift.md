# PKT-014 Operator Paper / Live Drift BFF Contract

## Purpose

Provide one backend-owned paper-vs-live comparison object so the UI does not infer drift metrics, threshold outcomes, or follow-up actions from raw policy text and unrelated evidence objects.

## Primary Read Route

- `GET /api/v1/operator/paper-live-drift/{runtime_id}`

Required response fields:

- `runtime_id`
- `plan_ref`
  - `plan_id`
  - `href`
- `artifact_ref`
  - `artifact_id`
  - `artifact_version`
- `paper_baseline` (nullable)
  - `captured_at`
  - `deployment_stage`
  - `window`
  - `metrics`
- `observed_state` (nullable)
  - `deployment_stage`
  - `runtime_status`
  - `observed_at`
  - `metrics`
- `drift_groups[]`
  - `group_id`
  - `label`
  - `status`
  - `metrics[]`
    - `metric_id`
    - `label`
    - `baseline_value`
    - `observed_value`
    - `delta`
    - `threshold`
    - `status`
    - `unit`
- `threshold_evaluation`
  - `overall_status`
  - `summary`
  - `breached_metric_ids[]`
- `evidence_refs[]`
  - `ref_id`
  - `type`
  - `href`
- `recommended_actions[]`
  - `action_id`
  - `label`
  - `reason`
  - `target_ref`
- `meta.snapshot_at`
- `meta.surfaces.paper_live_drift`
- `meta.surfaces.drift_report`
- `meta.surfaces.runtime_binding`
- `meta.surfaces.telemetry_summary`
- `meta.surfaces.telemetry_performance`
- `meta.surfaces.approval_decision`
- `meta.surfaces.incident`
- `meta.surfaces.evolution`

## Degraded-State Rules

- When `meta.surfaces.paper_live_drift = unavailable`, the UI must render the explicit unavailable state and must not invent fallback drift math in the browser.
- `paper_baseline` and `observed_state` may be `null` only when the drift report is unavailable. The UI must not synthesize them from policy text or adjacent routes.
- `recommended_actions[]` are backend-owned. The UI must not infer whether to open deployment review, incident response, or post-incident review from raw metric values.
- `drift_groups[]` and their metric ordering are backend-owned.

## Design Rules

- The Paper / Live Drift view reads from `GET /api/v1/operator/paper-live-drift/{runtime_id}` only.
- The comparison object is backend-owned and may draw from drift reports, runtime binding state, telemetry summaries, telemetry performance, approval decisions, incidents, and evolution evidence.
- Threshold labels and breach status are backend-owned and must be rendered as supplied.
- This packet is read-only. It does not add promotion, rollback, or evolution mutation authority.
- If any required field or `meta.surfaces.*` entry is missing, the frontend must emit a `bff-gap` handoff instead of inventing comparison logic locally.

## Example Payload

- `docs/examples/PKT-014-paper-live-drift.json`
