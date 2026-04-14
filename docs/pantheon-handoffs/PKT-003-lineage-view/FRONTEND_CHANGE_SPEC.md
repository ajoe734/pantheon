# PKT-003 Lineage View — Frontend Change Spec

## Feature

- Feature ID: `PKT-003-lineage-view`
- Screen ID: `screen-evolution-lineage`
- Workbench: Evolution Workbench
- Packet status: ready

## Summary

Build the **Lineage View** screen inside `front-ai-trading-system`. This screen gives operators a navigable artifact lineage surface so they can trace how artifacts were derived, promoted, and versioned — and understand which incidents or evolution decisions touch a given artifact chain. All data must come from the Pantheon BFF — no client-side graph construction.

## Files to Create or Modify

```
src/pages/evolution/LineageView.tsx                  — new Lineage View page
src/pages/evolution/LineageEdgeDetail.tsx             — new edge detail drawer component
src/pages/evolution/types.ts                          — add lineage-view types
src/lib/bffClient.ts                                  — add lineage-view fetch calls
```

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` calls in component files.

### Fetch lineage records list (LN-01)

```
GET /api/v1/lineage
Query params: artifact_id, page_token, page_size
```

Expected response shape (see `docs/examples/PKT-003-lineage-view.json` for a full example):

```typescript
interface LineageListResponse {
  items: LineageListItem[];
  page_info: { next_page_token: string | null };
  meta: { snapshot_at: string };
}

interface LineageListItem {
  artifact_id: string;
  edge_count: number;
  last_edge_at: string;
}
```

### Fetch lineage edge detail (LN-02)

```
GET /api/v1/lineage/edges/{edge_id}
```

Expected response shape:

```typescript
interface LineageEdgeDetailResponse {
  id: string;
  from_artifact_id: string;
  to_artifact_id: string;
  relationship: string;
  created_at: string;
  meta: { snapshot_at: string };
}
```

### Fetch lineage graph (LN-03)

```
GET /api/v1/lineage/graph
Query params: root_id (required), depth (integer, BFF clamps to 1–10)
```

Expected response shape:

```typescript
interface LineageGraphResponse {
  nodes: LineageNode[];
  edges: LineageGraphEdge[];
  meta: {
    snapshot_at: string;
    staleness?: { reason: string; served_from: string };
  };
}

interface LineageNode {
  artifact_id: string;
  artifact_version: string;
  artifact_type: string;
}

interface LineageGraphEdge {
  id: string;
  from_artifact_id: string;
  to_artifact_id: string;
  relationship: string;
}
```

## Component Structure

### `LineageView.tsx`

- Fetches `GET /api/v1/lineage` on mount.
- **Lineage list panel**: renders one row per item with `artifact_id`, `edge_count`, and `last_edge_at`.
  - Clicking a row fetches `GET /api/v1/lineage/graph?root_id={artifact_id}&depth={current_depth}` to load that artifact's lineage graph. It does **not** open the edge-detail drawer directly — list rows carry no `edge_id`.
- **Lineage Graph panel**: directed graph visualization of an artifact's lineage tree.
  - `depth` may be exposed as a user-selectable control (range 1–10); the BFF enforces the clamp — pass the value as-is without client-side clamping.
  - Do not expose `root_type` as a filter control — it is a no-op in the v1 BFF store.
  - Clicking a rendered graph edge opens the `LineageEdgeDetail` drawer, passing `edge_id` from `edges[].id` in the LN-03 response.
  - When `edges[]` in the graph response is empty for a given `root_id`, display "No lineage recorded for {artifact_id}" — do not render a blank graph canvas.
  - When `meta.staleness` is present in the graph response, render a non-dismissable staleness banner.
- **Degradation banner**: when `meta.staleness` is present on any response, render a non-dismissable banner.
- **Loading, empty, and error states**: explicit and visually distinct. No mock fallback.

### `LineageEdgeDetail.tsx`

- Receives `edge_id` as a prop (sourced from `lineage_graph.edges[].id` on graph-edge click); fetches `GET /api/v1/lineage/edges/{edge_id}` on open.
- Renders `id`, `from_artifact_id`, `to_artifact_id`, `relationship`, and `created_at`.
- No write actions.

## Constraints

- Use the existing BFF client only. Do not add raw `fetch` or `axios` in component files.
- Do not import or use any demo provider or mock data layer.
- Do not expose `root_type` as a filter UI control — it is a v1 no-op. If a future BFF version enables it, a new packet revision is required.
- `depth` may be exposed as a UI control but must be passed as-is to the BFF — do not clamp or validate client-side.
- Only `operator`, `approver`, `admin`, and `reviewer` role tokens are accepted by the BFF.
- No write actions on this screen — lineage is a read-only audit surface.
- If any required response field is absent, write `.coordination/requests/PKT-003-lineage-view-bff-gap.yaml` using `.coordination/requests/PKT-003-lineage-view-bff-gap.example.yaml` as the template and stop implementation.

## Degradation Handling

| State | Handling |
|---|---|
| `edges[]` empty in graph response | Display "No lineage recorded for {artifact_id}"; do not show a blank graph canvas |
| `meta.staleness` present | Render non-dismissable staleness banner; show available data with caveat |
| 404 on `{edge_id}` | Render "Lineage edge not found" in the drawer |
| Missing required field in any response | Emit `bff-gap` handoff; do not render the screen with invented state |

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/PKT-003-lineage-view-ui-done.yaml` using `.coordination/requests/PKT-003-lineage-view-ui-done.example.yaml` as the template.

## References

- Screen spec: `docs/screens/PKT-003-lineage-view.md`
- BFF contract: `docs/bff/PKT-003-lineage-view.md`
- Example payload: `docs/examples/PKT-003-lineage-view.json`
- Contract-ready: `.coordination/responses/PKT-003-lineage-view-contract-ready.yaml`
- Lovable UI task: `.coordination/responses/PKT-003-lineage-view-lovable-ui-task.yaml`
- BFF-gap template: `.coordination/requests/PKT-003-lineage-view-bff-gap.example.yaml`
- UI-done template: `.coordination/requests/PKT-003-lineage-view-ui-done.example.yaml`
