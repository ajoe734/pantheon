Build the `PKT-003-lineage-view` UI flow in `front-ai-trading-system` using only Pantheon APIs.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-003-lineage-view-bff-gap.yaml` using `.coordination/requests/PKT-003-lineage-view-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `lineage-view`.
Workbench: `evolution-workbench`.
Screen ID: `screen-evolution-lineage`.
Allowed endpoints:
- GET /api/v1/lineage
- GET /api/v1/lineage/edges/{edge_id}
- GET /api/v1/lineage/graph
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- build the Lineage list panel (row click loads the Lineage Graph; rows carry no edge_id)
- build the Lineage Graph panel using root_id and depth parameters; graph-edge click opens the edge detail drawer using edge_id from lineage_graph.edges[].id
- build the Lineage Edge Detail drawer triggered from graph-edge selection, not list-row selection
- do not expose root_type as a filter control (v1 BFF no-op)
- use only the existing BFF client
- render explicit empty state when lineage_graph.edges is empty (display "No lineage recorded" — not a blank canvas)
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-003-lineage-view-ui-done.yaml` using `.coordination/requests/PKT-003-lineage-view-ui-done.example.yaml` as the template. Sync that file back to GitHub and stop so Pantheon supervisor can pick up review/integration work automatically.
References:
- docs/screens/PKT-003-lineage-view.md
- docs/pantheon-handoffs/PKT-003-lineage-view/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-003-lineage-view.md
- docs/pantheon-handoffs/PKT-003-lineage-view
- docs/examples/PKT-003-lineage-view.json
