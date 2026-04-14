# PKT-003 Lineage View

## Classification

- Workbench: Evolution Workbench
- Screen ID: `screen-evolution-lineage`
- Feature ID: `PKT-003-lineage-view`
- Packet status: ready

## User Goal

Give an operator a navigable artifact lineage surface so they can trace how artifacts were derived, promoted, and versioned — and understand which incidents or evolution decisions touch a given artifact chain.

## Page Sections

- **Lineage list panel**: list of lineage records filtered by `artifact_id`. Each row shows `artifact_id`, `edge_count`, and `last_edge_at`. Clicking a row selects the artifact and loads the Lineage Graph panel for that `artifact_id`. Source: `GET /api/v1/lineage`.
- **Lineage Graph panel**: directed graph visualization of an artifact's lineage tree. Source: `GET /api/v1/lineage/graph?root_id={artifact_id}&depth={1-10}`. Clicking a graph edge opens the Lineage Edge Detail drawer for that edge.
  - `depth` is clamped to 1–10 by the BFF.
  - `root_type` filter is not applied in v1 store; the panel must document this as a known limitation and not expose it as a UI control.
- **Lineage Edge Detail drawer**: opens on graph-edge selection. Receives the `edge_id` from the selected graph edge (`lineage_graph.edges[].id`) and renders the single edge's `from_artifact_id`, `to_artifact_id`, `relationship`, and `created_at`. Source: `GET /api/v1/lineage/edges/{edge_id}`.
- **Degradation banner**: when `meta.staleness` is present or the BFF state is not `fresh`, a non-dismissable banner notes that lineage data may be stale.
- **Loading, empty, and error states**: explicit and visually distinct.

## Interaction Rules

- All production data comes from Pantheon BFF routes only.
- `root_type` must not be exposed as a filter control — it is a no-op in the v1 store. If future versions enable it, a new packet revision is required.
- `depth` may be exposed as a user-selectable control (range 1–10); the BFF enforces the clamp.
- If `edges` in the `lineage_graph` response is empty for a root artifact, display "No lineage recorded" with the artifact ID. Do not show a blank graph canvas.
- If a required field is absent from the BFF response, emit a `bff-gap` handoff.
- No write actions on this screen.

## Acceptance

- Lineage list renders from real BFF data with no mock rows.
- Edge Detail drawer opens from graph-edge selection (using `edge_id` from `lineage_graph.edges[].id`) and renders all required fields.
- Lineage Graph renders for a given `root_id` and respects the `depth` parameter.
- `root_type` filter is not exposed as a UI control.
- Empty and degraded states display explicit copy rather than a blank surface.
- Loading, empty, degraded, and error states are explicit and visually distinct.
- Front-end emits a `bff-gap` handoff if any expected field is absent.
