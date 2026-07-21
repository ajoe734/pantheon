Build the `PKT-003-inspiration-graph` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-003-inspiration-graph-bff-gap.yaml` using `.coordination/requests/PKT-003-inspiration-graph-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `inspiration-graph`.
Workbench: `evolution-workbench`.
Screen ID: `screen-evolution-inspiration-graph`.
Allowed endpoints:
- GET /api/v1/lineage/inspiration/{artifact_id}
Published Pantheon dependencies:
- .coordination/responses/PKT-003-inspiration-graph-contract-ready.yaml
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- build the Inspiration Graph panel from the BFF inspiration route only — route is live, production build may proceed
- build the Strategy Tags rail from strategy_tags[] in the BFF response
- build the Inspiration Edge Detail drawer triggered from graph-edge selection
- render explicit empty state when inspiration_edges[] is empty
- render non-dismissable degradation banner when meta.surfaces.inspiration is not fresh
- display meta.snapshot_at as the graph data timestamp
- do not traverse GET /api/v1/lineage or GET /api/v1/lineage/graph to synthesize the graph
Required feedback bundle:
- docs/pantheon-feedback/PKT-003-inspiration-graph/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/PKT-003-inspiration-graph/API_GAP_REQUESTS.json
- docs/pantheon-feedback/PKT-003-inspiration-graph/UI_DECISIONS.md
- docs/pantheon-feedback/PKT-003-inspiration-graph/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-003-inspiration-graph-ui-done.yaml` using `.coordination/requests/PKT-003-inspiration-graph-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/PKT-003-inspiration-graph-frontend-feedback.yaml` using `.coordination/requests/PKT-003-inspiration-graph-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/screens/PKT-003-inspiration-graph.md
- docs/pantheon-handoffs/PKT-003-inspiration-graph/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-003-inspiration-graph.md
- docs/pantheon-handoffs/PKT-003-inspiration-graph
- docs/examples/PKT-003-inspiration-graph.json
