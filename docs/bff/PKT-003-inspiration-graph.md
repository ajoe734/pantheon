# PKT-003 Inspiration Graph BFF Contract

## Status

**Blocked** — this route does not yet exist. This contract defines the required field shape that the BFF must implement before `PKT-003-inspiration-graph` can be unblocked for Lovable handoff.

## Purpose

Provide a BFF-composed inspiration graph for a given artifact so the Evolution Workbench Inspiration Graph screen can render creative lineage edges, influence weights, and strategy tags without client-side graph traversal from raw lineage endpoints.

## Required Route

### Get inspiration graph (EW-04)

- `GET /api/v1/lineage/inspiration/{artifact_id}`

Required response fields:

- `artifact_id` — string; mirrors the queried artifact ID
- `inspiration_edges[]` — array (may be empty):
  - `source_artifact_id` — string; upstream artifact that influenced this artifact
  - `relationship_type` — string; e.g. `"derived_from"`, `"inspired_by"`, `"strategy_applied"`
  - `influence_weight` — number; range 0.0–1.0; must be BFF-computed, not derived client-side
- `meta.snapshot_at` — ISO 8601 timestamp of when the BFF composed the graph
- `meta.surfaces.inspiration` — string; `"fresh"` | `"stale"` | `"unavailable"`

Optional response fields (include if available):

- `strategy_tags[]` — array of strings; strategy tags associated with the artifact's inspiration context
- `page_info.next_page_token` — nullable string; for paginated edge results if the BFF supports pagination

## Implementation Prerequisites

1. LN-03 lineage graph primitives (`GET /api/v1/lineage/graph`) must be stable.
2. The lineage `root_type` registry prerequisite must be resolved so that creative lineage edges are addressable by type.
3. The BFF must compose the inspiration view — the frontend must not synthesize it from raw lineage edge responses.

## UI Gating Rules

- When `meta.surfaces.inspiration != "fresh"`, render the degradation banner on the graph panel.
- When `inspiration_edges[]` is empty, render "No inspiration edges recorded for this artifact" with the artifact ID — do not show a blank canvas.
- `meta.snapshot_at` must be surfaced as a "data as of" timestamp on the graph panel.
- If any required field is absent from the response, emit a `bff-gap` handoff.

## Error Handling

- 404 on `{artifact_id}`: render "Artifact not found" with the artifact ID — do not attempt to synthesize an inspiration view.
- `meta.surfaces.inspiration = "unavailable"`: render the degradation banner and suppress graph rendering; do not fall back to raw lineage edges.
- Any missing required field in the response: emit a `bff-gap` handoff.

## Write Actions

None. Inspiration Graph is a read-only surface.

## Relationship to Other Routes

- This route must not be replaced by client-side traversal of `GET /api/v1/lineage` or `GET /api/v1/lineage/graph`.
- The BFF must enforce `influence_weight` computation and `relationship_type` labeling — the UI is not authoritative for these fields.
- `meta.surfaces.inspiration` feeds the shared degradation banner substrate defined in `PKT-005`.
