Build the `PKT-013-operator-home` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-013-operator-home-bff-gap.yaml` using `.coordination/requests/PKT-013-operator-home-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `operator-home-dashboard`.
Workbench: `operator-console`.
Screen ID: `screen-operator-home-dashboard`.
Allowed endpoints:
- GET /api/v1/operator/home
Published Pantheon dependencies:
- .coordination/responses/PKT-013-operator-home-contract-ready.yaml
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- build the Operator Home dashboard from the single operator-home route
- use only the existing BFF client
- keep cards and escalation shortcuts in backend-owned order
- distinguish unavailable or degraded state from a calm empty dashboard
- do not recreate this screen from alerts, health, incidents, governance, runtime, or kill-switch primitives
Required feedback bundle:
- docs/pantheon-feedback/PKT-013-operator-home/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/PKT-013-operator-home/API_GAP_REQUESTS.json
- docs/pantheon-feedback/PKT-013-operator-home/UI_DECISIONS.md
- docs/pantheon-feedback/PKT-013-operator-home/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-013-operator-home-ui-done.yaml` using `.coordination/requests/PKT-013-operator-home-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/PKT-013-operator-home-frontend-feedback.yaml` using `.coordination/requests/PKT-013-operator-home-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/screens/PKT-013-operator-home.md
- docs/pantheon-handoffs/PKT-013-operator-home/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-013-operator-home.md
- docs/pantheon-handoffs/PKT-013-operator-home
- docs/examples/PKT-013-operator-home.json
