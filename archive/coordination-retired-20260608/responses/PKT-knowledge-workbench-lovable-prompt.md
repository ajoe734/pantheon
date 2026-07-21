Build the `PKT-knowledge-workbench` UI flow in `front-ai-trading-system` using only Pantheon APIs.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-knowledge-workbench-bff-gap.yaml` using `.coordination/requests/PKT-knowledge-workbench-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `knowledge-workbench-overview`.
Allowed endpoints:
- GET /api/v1/workbench/knowledge
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- build the Knowledge Workbench landing page from the single overview route
- render module order, support refs, and next steps only from backend-owned payload fields
- do not invent registry, evidence-browser, or strategy-spec compare UI in this packet
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-knowledge-workbench-ui-done.yaml` using `.coordination/requests/PKT-knowledge-workbench-ui-done.example.yaml` as the template. Sync that file back to GitHub and stop so Pantheon supervisor can pick up review/integration work automatically.
References:
- docs/screens/PKT-knowledge-workbench.md
- docs/pantheon-handoffs/PKT-knowledge-workbench/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-knowledge-workbench.md
- docs/pantheon-handoffs/PKT-knowledge-workbench
- docs/examples/PKT-knowledge-workbench.json
