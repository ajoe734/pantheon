# PKT-014 Operator Paper / Live Drift — UI Decisions

- The screen is routed at `/operator/paper-live-drift/:runtimeId` and reads the
  runtime id only from the route parameter. It does not invent alternate drift
  selection state in query params or local storage.
- The screen fetches one backend-owned comparison object through
  `operatorApi.getPaperLiveDrift(runtimeId)` and does not fetch adjacent
  approval, incident, telemetry, or evolution surfaces directly.
- Required top-level fields and the full required PKT-014
  `meta.surfaces.paper_live_drift`, `drift_report`, `runtime_binding`,
  `telemetry_summary`, `telemetry_performance`, `approval_decision`,
  `incident`, and `evolution` keys are validated before render. Missing fields
  produce an explicit BFF-gap alert state instead of silent fallback logic.
- `plan_ref`, `evidence_refs[]`, and `recommended_actions[]` are rendered
  exactly as supplied. The UI does not rewrite hrefs into guessed browser
  routes.
- `drift_groups[]` and nested `metrics[]` are rendered in backend-owned order.
  No client-side sorting, grouping, or threshold evaluation is introduced.
- `threshold_evaluation` stays payload-owned. The UI renders the supplied
  overall status, summary, and breached metric ids without recomputing breach
  logic from raw values.
- `meta.surfaces.paper_live_drift = unavailable` renders the explicit
  unavailable treatment and suppresses the comparison snapshot cards and drift
  stack. The UI does not rebuild baseline or observed state from nearby data.
- Supporting degraded or unavailable surfaces are surfaced through the shared
  global degradation banner; they do not hide the reviewed payload-owned
  sections behind calm fallback content.
- `paper_baseline = null` and `observed_state = null` are accepted only when
  `meta.surfaces.paper_live_drift.status = unavailable`. Any other null
  combination is treated as a contract gap.
- The screen is read-only. It does not add promotion, rollback, incident, or
  evolution mutation CTAs outside the payload-owned `recommended_actions[]`
  rail.
