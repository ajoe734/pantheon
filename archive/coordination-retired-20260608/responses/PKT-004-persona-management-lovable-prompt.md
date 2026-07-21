Build the `PKT-004-persona-management` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-004-persona-management-bff-gap.yaml` using `.coordination/requests/PKT-004-persona-management-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `persona-management`.
Workbench: `persona-workbench`.
Screen ID: `screen-persona-management`.
Allowed endpoints:
- GET /api/v1/operator/persona-management/{persona_id}
- POST /api/v1/operator/commands
Published Pantheon dependencies:
- .coordination/responses/PKT-004-persona-management-contract-ready.yaml
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- build the Persona Management composed screen
- use only the existing BFF client
- do not add raw fetch calls in component files
- do not invent fields beyond this handoff packet
- render all action CTAs from backend-shaped allowedActions only
- display the degradation banner when any meta.surfaces entry is degraded or unavailable
- show degraded-panel placeholders per affected surface rather than hiding panels
Required feedback bundle:
- docs/pantheon-feedback/PKT-004-persona-management/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/PKT-004-persona-management/API_GAP_REQUESTS.json
- docs/pantheon-feedback/PKT-004-persona-management/UI_DECISIONS.md
- docs/pantheon-feedback/PKT-004-persona-management/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-004-persona-management-ui-done.yaml` using `.coordination/requests/PKT-004-persona-management-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/PKT-004-persona-management-frontend-feedback.yaml` using `.coordination/requests/PKT-004-persona-management-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/screens/PKT-004-persona-management.md
- docs/pantheon-handoffs/PKT-004-persona-management/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-004-persona-management.md
- docs/pantheon-handoffs/PKT-004-persona-management
- docs/examples/PKT-004-persona-management.json
