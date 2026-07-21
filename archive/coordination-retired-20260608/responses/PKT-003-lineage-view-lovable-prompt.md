Build the `PKT-003-lineage-view` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-003-lineage-view-bff-gap.yaml` using `.coordination/requests/PKT-003-lineage-view-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `lineage-view`.
Workbench: `evolution-workbench`.
Screen ID: `screen-evolution-lineage`.
Allowed endpoints:
- GET /api/v1/lineage
- GET /api/v1/lineage/edges/{edge_id}
- GET /api/v1/lineage/graph
Published Pantheon dependencies:
- .coordination/responses/PKT-003-lineage-view-contract-ready.yaml
- .coordination/responses/PKT-003-lineage-view-backend-delivery.yaml
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
Required feedback bundle:
- docs/pantheon-feedback/PKT-003-lineage-view/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/PKT-003-lineage-view/API_GAP_REQUESTS.json
- docs/pantheon-feedback/PKT-003-lineage-view/UI_DECISIONS.md
- docs/pantheon-feedback/PKT-003-lineage-view/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-003-lineage-view-ui-done.yaml` using `.coordination/requests/PKT-003-lineage-view-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/PKT-003-lineage-view-frontend-feedback.yaml` using `.coordination/requests/PKT-003-lineage-view-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/screens/PKT-003-lineage-view.md
- docs/pantheon-handoffs/PKT-003-lineage-view/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-003-lineage-view.md
- docs/pantheon-handoffs/PKT-003-lineage-view
- docs/examples/PKT-003-lineage-view.json
