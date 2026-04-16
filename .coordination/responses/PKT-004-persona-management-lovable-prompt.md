Build the `PKT-004-persona-management` UI flow in `front-ai-trading-system` using only Pantheon APIs.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-004-persona-management-bff-gap.yaml` using `.coordination/requests/PKT-004-persona-management-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `persona-management`.
Workbench: `persona-workbench`.
Screen ID: `screen-persona-management`.
Allowed endpoints:
- GET /api/v1/operator/persona-management/{persona_id}
- POST /api/v1/operator/commands
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
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-004-persona-management-ui-done.yaml` using `.coordination/requests/PKT-004-persona-management-ui-done.example.yaml` as the template. Sync that file back to GitHub and stop so Pantheon supervisor can pick up review/integration work automatically.
References:
- docs/screens/PKT-004-persona-management.md
- docs/pantheon-handoffs/PKT-004-persona-management/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-004-persona-management.md
- docs/pantheon-handoffs/PKT-004-persona-management
- docs/examples/PKT-004-persona-management.json
