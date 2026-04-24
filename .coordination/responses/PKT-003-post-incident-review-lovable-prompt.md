Resume the `PKT-003-post-incident-review` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already aligned the blocking BFF envelopes for this screen. Use the
published contract, example payload, and frontend change spec as the source of
truth for this resumed implementation cycle.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-003-post-incident-review-bff-gap.yaml` using `.coordination/requests/PKT-003-post-incident-review-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `post-incident-review-console`.
Workbench: `operator-console`.
Screen ID: `screen-operator-post-incident-review`.
Allowed endpoints:
- GET /api/v1/incidents
- GET /api/v1/operator/post-incident-review/{incident_id}
- GET /api/v1/postmortems
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- build the Post-Incident Review Console list and composed detail panels
- use only the existing BFF client
- do not add raw fetch calls in component files
- do not invent fields beyond this handoff packet
- respect meta.surfaces gating for each evidence panel
- display the degradation banner when any meta.surfaces entry is degraded or unavailable
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-003-post-incident-review-ui-done.yaml` using `.coordination/requests/PKT-003-post-incident-review-ui-done.example.yaml` as the template. Sync that file back to GitHub and stop so Pantheon supervisor can pick up review/integration work automatically.
References:
- docs/screens/PKT-003-post-incident-review-console.md
- docs/pantheon-handoffs/PKT-003-post-incident-review/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-003-post-incident-review-console.md
- docs/pantheon-handoffs/PKT-003-post-incident-review
- docs/examples/PKT-003-post-incident-review-console.json
