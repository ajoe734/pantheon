# PKT-002 Incident Detail

## Classification

- Workbench: Operator Console
- Screen ID: `screen-operator-incident-detail`
- Feature ID: `PKT-002-incident-detail`
- Packet status: ready

## User Goal

Give an operator a complete view of a single active incident — the incident record, affected bindings and personas, current kill switch state, and available emergency actions — without joining these surfaces client-side. The operator should be able to assess severity, understand scope, and open the action drawer from this screen.

## Page Sections

- **Incident summary panel**: top-level incident fields — `incident_id`, `title`, `severity`, `status`, `artifact_id`, `artifact_version`, `runtime_id`, `trace_id`, `opened_at`. Source: `data.incident` in the composed view.
- **Affected bindings panel**: list of `data.affected_bindings[]` showing `binding_id`, `persona_id`, `capital_pool_id`, `stage`, and `binding_status`. When `meta.surfaces.affected_bindings = ok` and the list is empty, renders "No affected bindings recorded". When `meta.surfaces.affected_bindings = degraded`, renders an explicit degraded panel showing any available binding records followed by "Affected bindings data is partially unavailable — [meta.degradation.affected_bindings_reason]". Never renders a generic "no data" state for a degraded read.
- **Kill Switch status panel**: current kill switch state from `data.kill_switch` — `status`, `last_triggered_at`, `last_confirmed_at`, `active_commands[]`. Renders with staleness note when `meta.surfaces.kill_switch = degraded`. Renders "Kill switch status unavailable" when `meta.surfaces.kill_switch = unavailable`.
- **Action entry strip**: a read-only summary of available emergency actions derived from `allowedActions`. Each allowed action shows its name and a short rationale. The strip includes an **Open Action Drawer** CTA to enter the Incident Action Drawer surface.
- **Degradation banner**: when any `meta.surfaces` entry has `status != "ok"`, a non-dismissable banner names the degraded surface and disables the relevant CTAs.
- **Loading, empty, degraded, and error states**: explicit and visually distinct with no mock fallback.

## Interaction Rules

- All production data comes from `GET /api/v1/operator/incident-response/{incident_id}`.
- The UI must not re-fetch individual surfaces (incidents, kill switch, bindings) separately when the composed view is available.
- CTA visibility for the action entry strip comes from `allowedActions` only — the UI must not derive emergency action eligibility locally.
- If `meta.surfaces.kill_switch = degraded`, render the last known kill switch state with a staleness note. Do not hide the panel.
- If `meta.surfaces.kill_switch = unavailable`, render "Kill switch status unavailable". Do not assume any kill switch state.
- If `data.affected_bindings` is empty and `meta.surfaces.affected_bindings = ok`, render "No affected bindings recorded".
- If `meta.surfaces.affected_bindings = degraded`, render an explicit degraded panel: show whatever binding records are available, then display "Affected bindings data is partially unavailable — [meta.degradation.affected_bindings_reason]". Do not collapse a degraded read into an empty-success state.
- If a required field is absent from the BFF response, the UI must emit a `bff-gap` handoff instead of inventing local state.
- The **Open Action Drawer** CTA is disabled when `allowedActions.canOpenActionDrawer = false`.

## Acceptance

- Incident summary panel renders all required fields from the composed view with no mock data.
- Affected bindings panel renders real data; shows explicit empty-state copy when `meta.surfaces.affected_bindings = ok` and the list is empty; shows an explicit named degraded panel when `meta.surfaces.affected_bindings = degraded`.
- Kill switch status panel handles `ok`, `degraded`, and `unavailable` surface states explicitly.
- Action entry strip derives all CTA visibility from `allowedActions` — no local eligibility logic.
- Degradation banner renders when any `meta.surfaces` entry is not `ok`.
- Loading, empty, degraded, and error states are explicit and visually distinct.
- Front-end emits a `bff-gap` handoff if any expected `meta.surfaces` key is absent from the response.
