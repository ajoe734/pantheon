Build the `KW-04-insight-cards` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/KW-04-insight-cards-bff-gap.yaml` using `.coordination/requests/KW-04-insight-cards-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `knowledge-insight-cards`.
Workbench: `knowledge-workbench`.
Screen ID: `screen-knowledge-insight-card-list`.
Allowed endpoints:
- GET /api/v1/knowledge/insights
- GET /api/v1/knowledge/insights/{insight_id}
Published Pantheon dependencies:
- .coordination/responses/KW-04-insight-cards-contract-ready.yaml
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- build the Insight Cards list and detail surfaces in front-ai-trading-system
- use only the existing BFF client
- do not add raw fetch calls in component files
- render backend-owned filter metadata, confidence, scope context, and supersession data as published
- do not recreate the insight-card synthesis pipeline in the browser
- emit a bff-gap handoff if any required field is absent
- publish ui-done and frontend-feedback from one Git-visible commit
Required feedback bundle:
- docs/pantheon-feedback/KW-04-insight-cards/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/KW-04-insight-cards/API_GAP_REQUESTS.json
- docs/pantheon-feedback/KW-04-insight-cards/UI_DECISIONS.md
- docs/pantheon-feedback/KW-04-insight-cards/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/KW-04-insight-cards-ui-done.yaml` using `.coordination/requests/KW-04-insight-cards-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/KW-04-insight-cards-frontend-feedback.yaml` using `.coordination/requests/KW-04-insight-cards-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/pantheon-handoffs/KW-04-insight-cards/FRONTEND_CHANGE_SPEC.md
- docs/bff/KW-04-insight-cards.md
- docs/pantheon-handoffs/KW-04-insight-cards
- docs/examples/KW-04-insight-cards.json
