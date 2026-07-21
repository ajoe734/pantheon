Build the `PKT-003-inspiration-graph` UI flow in `front-ai-trading-system` using only the Pantheon BFF inspiration route.

**IMPORTANT**: The BFF route `GET /api/v1/lineage/inspiration/{artifact_id}` is **live** and returning the published field shape. Build the production page now. Do not synthesize any graph data client-side.

If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-003-inspiration-graph-bff-gap.yaml` using `.coordination/requests/PKT-003-inspiration-graph-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.

Screen: `inspiration-graph`.
Workbench: `evolution-workbench`.
Screen ID: `screen-evolution-inspiration-graph`.

Allowed endpoints:
- GET /api/v1/lineage/inspiration/{artifact_id}

Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- do not traverse GET /api/v1/lineage or GET /api/v1/lineage/graph to synthesize the graph
- if any required field is missing, emit a bff-gap handoff instead of mocking

Acceptance:
- build the Inspiration Graph panel from GET /api/v1/lineage/inspiration/{artifact_id} only — no raw lineage traversal
- render inspiration_edges[] as a directed graph; map influence_weight to edge visual weight (e.g. thickness or opacity)
- build the Inspiration Edge Detail drawer from graph-edge click; render source_artifact_id, relationship_type, and influence_weight
- build the Strategy Tags rail from strategy_tags[] in the BFF response (display-only, not computed client-side)
- display meta.snapshot_at as the graph data timestamp ("data as of")
- render non-dismissable degradation banner when meta.surfaces.inspiration is not "fresh"
- render "No inspiration edges recorded for {artifact_id}" when inspiration_edges[] is empty — do not show a blank canvas
- render "Artifact not found" on 404 — do not attempt to synthesize a view

Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-003-inspiration-graph-ui-done.yaml` using `.coordination/requests/PKT-003-inspiration-graph-ui-done.example.yaml` as the template. Sync that file back to GitHub and stop so Pantheon supervisor can pick up review/integration work automatically.

References:
- docs/screens/PKT-003-inspiration-graph.md
- docs/pantheon-handoffs/PKT-003-inspiration-graph/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-003-inspiration-graph.md
- docs/examples/PKT-003-inspiration-graph.json
