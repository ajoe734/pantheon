Resume the `PKT-002-incident-home` follow-up in `front-ai-trading-system`.
Pantheon re-reviewed the current Incident Home return and confirmed that the
screen logic is still statically aligned with the published PKT-002 contract
and the PKT-005 split-read degradation-banner rules. Pantheon's incident-home
acceptance slice also still passes locally on the unchanged contract.
Do not rebuild the screen from scratch. No new Pantheon endpoint, runtime
layer, or contract change is required in this pass.
If backend fields are missing or the live payload diverges from the synced
contract, stop implementation and write
`.coordination/requests/PKT-002-incident-home-bff-gap.yaml` using
`.coordination/requests/PKT-002-incident-home-bff-gap.example.yaml` as the
template. Then sync that file back through the normal front-repo flow so
Pantheon supervisor can continue the loop.

Screen: `incident-home`.
Workbench: `operator-console`.
Screen ID: `screen-operator-incident-home`.

Allowed endpoints:
- GET /api/v1/incidents
- GET /api/v1/kill-switch/status

Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
- keep the existing Pantheon contract unchanged; do not ask for new endpoints,
  shadow state, or alternate route families
- refresh `docs/pantheon-feedback/PKT-002-incident-home/` so its reviewed
  commit anchors and route narration truthfully match the reviewed source
  snapshot
- change Incident Home row navigation to
  `/operator/incidents/${incident_id}`
- if the reviewed source commit changes while fixing the route or feedback
  bundle, republish
  `.coordination/requests/PKT-002-incident-home-frontend-feedback.yaml` and
  `.coordination/requests/PKT-002-incident-home-ui-done.yaml` from the same
  Git-visible front commit
- commit `docs/pantheon-feedback/PKT-002-incident-home/` in that same front
  commit
- set both request `source_commit` values to that exact final commit SHA
- do not use `HEAD` as `source_commit`

Acceptance:
- keep Incident Home on `GET /api/v1/incidents` and
  `GET /api/v1/kill-switch/status` only
- keep split-read degradation handling on the shared banner helpers; do not
  introduce local shadow state
- use only the existing BFF client
- do not add raw fetch calls in component files
- row selection navigates to the mounted
  `/operator/incidents/:incidentId` route
- the feedback bundle truthfully references the reviewed source commit and the
  mounted `/operator/incidents` route family

Completion handoff:
- If the reviewed source commit changes, publish
  `.coordination/requests/PKT-002-incident-home-frontend-feedback.yaml` and
  `.coordination/requests/PKT-002-incident-home-ui-done.yaml` together from
  one final Git-visible front commit with matching `source_commit` values.
- Sync those files plus `docs/pantheon-feedback/PKT-002-incident-home/` back
  to GitHub and stop so Pantheon supervisor can pick up the next review step
  automatically.

References:
- docs/screens/PKT-002-incident-home.md
- docs/pantheon-handoffs/PKT-002-incident-home/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-002-incident-home.md
- docs/pantheon-handoffs/PKT-002-incident-home
- docs/examples/PKT-002-incident-home.json
- .coordination/responses/PKT-002-incident-home-contract-ready.yaml
- .coordination/responses/PKT-002-incident-home-backend-delivery.yaml
