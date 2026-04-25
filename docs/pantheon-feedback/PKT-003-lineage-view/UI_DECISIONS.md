# PKT-003 Lineage View — UI Decisions

- The screen is currently mounted at `/lineage`. Integration under the Evolution Workbench at `/evolution/lineage` is deferred until the workbench shell route is finalized; it is recorded as a follow-up item, not part of this delivery.
- `root_id` is held in local React component state (`selectedArtifactId`). URL-addressable `?root_id=...` query-param wiring is a follow-up item deferred until the workbench integration is in place; it was not implemented in this cycle.
- The Lineage List panel reads only through the BFF client via `GET /api/v1/lineage`. Artifact-id filter is passed to Pantheon as the documented `artifact_id` query param; no client-side filtering.
- Row click in the Lineage List triggers `GET /api/v1/lineage/graph?root_id={artifact_id}&depth={current_depth}` — it does **not** open the edge detail drawer directly. List rows carry no `edge_id` and cannot trigger the drawer.
- The Lineage Graph panel passes `depth` as a user-selectable control (1–10) without client-side clamping. The BFF enforces the clamp per the published contract.
- `root_type` is not exposed as a UI filter control. It is documented in the component comment as a v1 BFF no-op. A new packet revision is required before it can be surfaced.
- Graph-edge click opens `LineageEdgeDetail` passing `edge_id` from `lineage_graph.edges[].id`. The drawer fetches `GET /api/v1/lineage/edges/{edge_id}` on open. It does not reuse any data already in the graph response.
- When `lineage_graph.edges[]` is empty for a root artifact, the graph area renders "No lineage recorded for {artifact_id}" — no blank canvas is shown.
- When `meta.staleness` is present in any BFF response, a non-dismissable staleness banner is rendered above the affected panel.
- Missing required fields are treated as an explicit BFF-gap alert state, not silently omitted or replaced with defaults.
- No write actions exist on this screen — lineage is a read-only audit surface.
- The prior BFF-gap (filed in `.coordination/requests/PKT-003-lineage-view-bff-gap.yaml`) is resolved. The implementation targets the corrected BFF contract delivered by BP5-SVC-010.
