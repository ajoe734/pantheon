Build the `PKT-003-post-incident-review` UI flow in `front-ai-trading-system` using only Pantheon APIs.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-003-post-incident-review-bff-gap.yaml` using `.coordination/requests/PKT-003-post-incident-review-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `post-incident-review-console`.
Allowed endpoints:
- GET /api/v1/incidents
- GET /api/v1/operator/post-incident-review/{incident_id}
- GET /api/v1/postmortems
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
- all meta.surfaces gating must come from the BFF response; do not derive locally
Acceptance:
- build the Post-Incident Review list panel and composed detail panel
- use only the existing BFF client
- do not add raw fetch calls in component files
- do not invent endpoint fields beyond this handoff packet
- render postmortem, evolution, lineage, and telemetry panels with explicit degraded states
- display the degradation banner when any meta.surfaces entry is not ok
- render loading, empty, degraded, and error states as explicitly distinct
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-003-post-incident-review-ui-done.yaml` using `.coordination/requests/PKT-003-post-incident-review-ui-done.example.yaml` as the template. Sync that file back to GitHub and stop so Pantheon supervisor can pick up review/integration work automatically.
References:
- docs/bff/PKT-003-post-incident-review-console.md
- docs/examples/PKT-003-post-incident-review-console.json
