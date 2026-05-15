# PKT-003 Inspiration Graph

## Classification

- Workbench: Evolution Workbench
- Screen ID: `screen-evolution-inspiration-graph`
- Feature ID: `PKT-003-inspiration-graph`
- Packet status: **route-live** — BFF route `GET /api/v1/lineage/inspiration/{artifact_id}` is confirmed live; UI work is unblocked
- Task: `EW-04-OPEN-001`

## Contract Note

The route contract and composed object field shape are confirmed. The BFF route is now live and returning the published field shape. UI implementation may proceed.

The UI must not construct an inspiration graph from raw lineage edges client-side. The BFF must compose the inspiration graph before it reaches the frontend.

## User Goal

Give an operator a creative lineage view for a specific artifact — showing which upstream artifacts, strategy tags, and evolution decisions influenced it — so they can understand how the current artifact's design was shaped without manually traversing raw lineage edges.

## Page Sections

- **Artifact selector**: input or breadcrumb that accepts an `artifact_id`. Drives all panels on this screen. When empty, renders an explicit prompt to enter an artifact ID.
- **Inspiration Graph panel**: directed graph showing the selected artifact at center with upstream `inspiration_edges[]` radiating outward. Each edge encodes `source_artifact_id`, `relationship_type`, and `influence_weight`. Edge weight is visualized (e.g., thickness or opacity). Clicking an edge opens the Inspiration Edge Detail drawer. Source: `GET /api/v1/lineage/inspiration/{artifact_id}`.
- **Strategy Tags rail**: horizontal tag strip showing the strategy tags associated with the artifact's inspiration context, derived from the BFF inspiration response (not from client-side graph traversal). Tags are display-only.
- **Inspiration Edge Detail drawer**: slides open on edge selection. Renders `source_artifact_id`, `relationship_type`, and `influence_weight` from the selected edge.
- **Degradation banner**: when `meta.surfaces.inspiration` indicates staleness or the BFF state is not `fresh`, a non-dismissable banner notes which inspiration surface is affected. Banner copy must not be derived from client-side graph state.
- **Loading, empty, and error states**: explicit and visually distinct with no mock fallback.

## Interaction Rules

- All data comes from the Pantheon BFF inspiration route only. Do not traverse `GET /api/v1/lineage` or `GET /api/v1/lineage/graph` to synthesize an inspiration view client-side.
- `meta.snapshot_at` from the BFF response must be rendered as a "data as of" timestamp on the graph panel.
- When `inspiration_edges[]` is empty for a given artifact, render "No inspiration edges recorded for this artifact" with the artifact ID. Do not show a blank graph canvas.
- If a required response field is absent from the BFF inspiration route, emit a `bff-gap` handoff.
- No write actions on this screen.

## Field Shape Required from BFF

The BFF inspiration route must return:

```
artifact_id            — string; the queried artifact
inspiration_edges[]    — array; may be empty
  source_artifact_id   — string; upstream artifact
  relationship_type    — string; e.g. "derived_from", "inspired_by", "strategy_applied"
  influence_weight     — number (0.0–1.0)
meta.snapshot_at       — ISO timestamp
meta.surfaces.inspiration — staleness signal string; "fresh" | "stale" | "unavailable"
```

The UI must not infer `influence_weight` or `relationship_type` from raw lineage edge data.

## Acceptance

- Inspiration Graph panel renders from `GET /api/v1/lineage/inspiration/{artifact_id}` — no client-side graph construction from raw lineage endpoints.
- Graph edges reflect `relationship_type` and `influence_weight` from the BFF response.
- Inspiration Edge Detail drawer opens on edge selection and renders all required fields.
- Strategy Tags rail renders tags from the BFF response only.
- Degradation banner renders when `meta.surfaces.inspiration` is not `fresh`.
- Empty state ("No inspiration edges recorded") renders when `inspiration_edges[]` is empty.
- `meta.snapshot_at` is displayed as the graph data timestamp.
- Loading, empty, degraded, and error states are explicit and visually distinct.
- Front-end emits a `bff-gap` handoff if any expected field is absent.
