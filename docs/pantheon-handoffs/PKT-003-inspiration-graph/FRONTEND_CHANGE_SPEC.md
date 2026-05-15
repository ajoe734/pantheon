# PKT-003 Inspiration Graph — Frontend Change Spec

## Feature

- Feature ID: `PKT-003-inspiration-graph`
- Screen ID: `screen-evolution-inspiration-graph`
- Workbench: Evolution Workbench
- Packet status: route-live — BFF route confirmed live, UI implementation is unblocked
- Task: `EW-04-OPEN-001`

## Readiness Gate

`GET /api/v1/lineage/inspiration/{artifact_id}` is confirmed live and returning the published field shape. Production UI build is unblocked. The shell placeholder is no longer required.

## Summary

Build the **Inspiration Graph** screen inside `front-ai-trading-system`. This screen gives operators an artifact-centered creative lineage view showing which upstream artifacts, strategy tags, and evolution decisions influenced a given artifact. All data must come from the Pantheon BFF inspiration route — no client-side graph construction from raw lineage endpoints.

## Files to Create or Modify

```
src/pages/evolution/InspirationGraph.tsx         — new Inspiration Graph page
src/pages/evolution/InspirationEdgeDetail.tsx    — new edge detail drawer component
src/pages/evolution/types.ts                     — add inspiration-graph types
src/lib/bffClient.ts                             — add inspiration-graph fetch call
```

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` calls in component files.

### Fetch inspiration graph (EW-04)

```
GET /api/v1/lineage/inspiration/{artifact_id}
Path param: artifact_id (required)
```

Expected response shape (see `docs/examples/PKT-003-inspiration-graph.json` for a full example):

```typescript
interface InspirationGraphResponse {
  artifact_id: string;
  inspiration_edges: InspirationEdge[];
  strategy_tags?: string[];
  meta: {
    snapshot_at: string;
    surfaces: {
      inspiration: "fresh" | "stale" | "unavailable";
    };
  };
}

interface InspirationEdge {
  source_artifact_id: string;
  relationship_type: string; // e.g. "derived_from" | "inspired_by" | "strategy_applied"
  influence_weight: number;  // 0.0–1.0; BFF-computed, do not derive client-side
}
```

## Component Structure

### `InspirationGraph.tsx`

- Accepts `artifact_id` from the route param `/evolution/inspiration/:artifact_id`.
- When `artifact_id` is absent or empty, renders an explicit prompt to enter an artifact ID. Do not render an empty graph canvas.
- Fetches `GET /api/v1/lineage/inspiration/{artifact_id}` on mount and on `artifact_id` change.
- **Inspiration Graph panel**: directed graph with the selected artifact at center and `inspiration_edges[]` radiating outward. Edge visual weight maps to `influence_weight` (e.g., line thickness or opacity). Clicking an edge opens the `InspirationEdgeDetail` drawer, passing the selected edge object.
- **Strategy Tags rail**: horizontal display-only tag strip from `strategy_tags[]` in the BFF response. Do not compute or label tags client-side.
- **"Data as of" timestamp**: render `meta.snapshot_at` on the graph panel as a visible "data as of" label.
- **Degradation banner**: when `meta.surfaces.inspiration` is not `"fresh"`, render a non-dismissable banner. Banner copy must not be derived from client-side graph state.
- **Empty state**: when `inspiration_edges[]` is empty, display "No inspiration edges recorded for {artifact_id}". Do not show a blank graph canvas.
- **Loading, empty, and error states**: explicit and visually distinct. No mock fallback.

### `InspirationEdgeDetail.tsx`

- Receives the selected `InspirationEdge` object as a prop; opened from graph-edge click in `InspirationGraph.tsx`.
- Renders `source_artifact_id`, `relationship_type`, and `influence_weight` from the edge object.
- No write actions.

## Constraints

- Use the existing BFF client only. Do not add raw `fetch` or `axios` in component files.
- Do not import or use any demo provider or mock data layer.
- Do not traverse `GET /api/v1/lineage` or `GET /api/v1/lineage/graph` to reconstruct inspiration edges, influence weights, or strategy tags. The BFF inspiration route is the only permitted data source for this screen.
- `influence_weight` and `relationship_type` must come from the BFF response. The UI is not authoritative for these fields.
- No write actions on this screen — Inspiration Graph is a read-only surface.
- If any required response field is absent, write `.coordination/requests/PKT-003-inspiration-graph-bff-gap.yaml` using `.coordination/requests/PKT-003-inspiration-graph-bff-gap.example.yaml` as the template and stop implementation.

## Degradation Handling

| State | Handling |
|---|---|
| `meta.surfaces.inspiration` is `"stale"` | Render non-dismissable staleness banner; show available data with caveat |
| `meta.surfaces.inspiration` is `"unavailable"` | Render degradation banner and suppress graph rendering; do not fall back to raw lineage edges |
| `inspiration_edges[]` empty | Display "No inspiration edges recorded for {artifact_id}"; do not show a blank graph canvas |
| 404 on `{artifact_id}` | Render "Artifact not found" with the artifact ID; do not attempt to synthesize a view |
| Missing required field in response | Emit `bff-gap` handoff; do not render with invented state |

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/PKT-003-inspiration-graph-ui-done.yaml` using `.coordination/requests/PKT-003-inspiration-graph-ui-done.example.yaml` as the template.

## References

- Screen spec: `docs/screens/PKT-003-inspiration-graph.md`
- BFF contract: `docs/bff/PKT-003-inspiration-graph.md`
- Example payload: `docs/examples/PKT-003-inspiration-graph.json`
- Contract-ready: `.coordination/responses/EW-04-inspiration-graph-contract-ready.yaml`
- Lovable UI task: `.coordination/responses/EW-04-inspiration-graph-lovable-ui-task.yaml`
- BFF-gap template: `.coordination/requests/PKT-003-inspiration-graph-bff-gap.example.yaml`
- UI-done template: `.coordination/requests/PKT-003-inspiration-graph-ui-done.example.yaml`
- Packet family: `docs/pantheon-handoffs/EW-004-evolution-workbench/PACKET_FAMILY.md`
