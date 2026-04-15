Build the `PKT-002-incident-detail` UI flow in `front-ai-trading-system` using only Pantheon APIs.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-002-incident-detail-bff-gap.yaml` using `.coordination/requests/PKT-002-incident-detail-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `incident-detail`.
Allowed endpoints:
- GET /api/v1/operator/incident-response/{incident_id}
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- build the Incident Detail composed view panel from GET /api/v1/operator/incident-response/{incident_id}
- use only the existing BFF client
- do not add raw fetch calls in component files
- do not invent fields beyond this handoff packet
- render all CTAs from backend-shaped allowedActions only
- render each degraded surface with explicit named copy; never show a generic empty state
- display the degradation banner when any meta.surfaces entry is degraded or unavailable
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-002-incident-detail-ui-done.yaml` using `.coordination/requests/PKT-002-incident-detail-ui-done.example.yaml` as the template. Sync that file back to GitHub and stop so Pantheon supervisor can pick up review/integration work automatically.
