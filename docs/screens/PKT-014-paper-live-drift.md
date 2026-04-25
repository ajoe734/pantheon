# PKT-014 Operator Paper / Live Drift View

## Classification

- Workbench: Operator Console
- Screen ID: `screen-operator-paper-live-drift`
- Feature ID: `PKT-014-paper-live-drift`
- Packet status: ready

## User Goal

Give operators one truthful review screen for paper-vs-live drift so they can understand what changed, why it breached, and which existing owner surface should handle the next decision.

## Page Sections

- **Comparison header**: renders runtime identity, plan ref, artifact ref, and the paper/observed stage boundary.
- **Threshold summary**: renders `threshold_evaluation`.
- **Drift group stack**: renders `drift_groups[]` in backend-owned order.
- **Evidence drawer**: renders `evidence_refs[]` exactly as supplied.
- **Recommended actions rail**: renders `recommended_actions[]` exactly as supplied.
- **Unavailable drift state**: when `meta.surfaces.paper_live_drift = unavailable`, show the explicit unavailable treatment instead of an empty calm comparison.

## Interaction Rules

- All production data comes from `GET /api/v1/operator/paper-live-drift/{runtime_id}`.
- The UI must not derive drift metrics from raw policy documents, approval decisions, incident records, or telemetry primitives.
- Threshold outcomes, breach labels, and group ordering are backend-owned.
- Existing-owner navigation uses `recommended_actions[]` and `plan_ref` only.
- This screen is read-only and must not add new promotion, rollback, or mutation CTAs.

## Acceptance

- The view renders one backend-owned comparison route.
- `drift_groups[]` render in backend-owned order.
- `recommended_actions[]` are rendered exactly as supplied.
- `meta.surfaces.paper_live_drift = unavailable` renders the explicit unavailable state.
- Any degraded or unavailable supporting surface also triggers the shared global degradation banner.
