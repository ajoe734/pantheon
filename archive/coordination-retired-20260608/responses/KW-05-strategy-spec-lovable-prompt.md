Build the `KW-05-strategy-spec` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/KW-05-strategy-spec-bff-gap.yaml` using `.coordination/requests/KW-05-strategy-spec-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `knowledge-strategy-spec`.
Workbench: `knowledge-workbench`.
Screen ID: `screen-knowledge-strategy-spec-list`.
Allowed endpoints:
- GET /api/v1/knowledge/strategy-specs
- GET /api/v1/knowledge/strategy-specs/{strategy_id}
- GET /api/v1/knowledge/strategy-specs/{strategy_id}/versions
- GET /api/v1/knowledge/strategy-specs/{strategy_id}/compare
Published Pantheon dependencies:
- .coordination/responses/KW-05-strategy-spec-contract-ready.yaml
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- build the Strategy Spec list, detail, version-history, and compare surfaces in front-ai-trading-system
- use only the existing BFF client
- do not add raw fetch calls in component files
- render canonical version identity, lifecycle, ancestry, citation links, and compare output from Pantheon BFF only
- do not reconstruct version history or compare semantics client-side
- emit a bff-gap handoff if any required field is absent
- publish ui-done and frontend-feedback from one Git-visible commit
Required feedback bundle:
- docs/pantheon-feedback/KW-05-strategy-spec/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/KW-05-strategy-spec/API_GAP_REQUESTS.json
- docs/pantheon-feedback/KW-05-strategy-spec/UI_DECISIONS.md
- docs/pantheon-feedback/KW-05-strategy-spec/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/KW-05-strategy-spec-ui-done.yaml` using `.coordination/requests/KW-05-strategy-spec-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/KW-05-strategy-spec-frontend-feedback.yaml` using `.coordination/requests/KW-05-strategy-spec-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/pantheon-handoffs/KW-05-strategy-spec/FRONTEND_CHANGE_SPEC.md
- docs/bff/KW-05-strategy-spec.md
- docs/pantheon-handoffs/KW-05-strategy-spec
- docs/examples/KW-05-strategy-spec.json
