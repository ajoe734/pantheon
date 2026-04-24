Build the `KW-03-evidence-refs` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/KW-03-evidence-refs-bff-gap.yaml` using `.coordination/requests/KW-03-evidence-refs-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `knowledge-evidence-refs`.
Workbench: `knowledge-workbench`.
Screen ID: `screen-knowledge-evidence-list`.
Allowed endpoints:
- GET /api/v1/knowledge/evidence
- GET /api/v1/knowledge/evidence/{ref_id}
Published Pantheon dependencies:
- .coordination/responses/KW-03-evidence-refs-contract-ready.yaml
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- build the Evidence Refs list and detail surfaces in front-ai-trading-system
- use only the existing BFF client
- do not add raw fetch calls in component files
- render credibility, linked-object summary, and resolved-link semantics exactly as returned by the BFF
- do not construct URLs from source_ref, storage_ref, or raw ids
- emit a bff-gap handoff if any required field is absent
- publish ui-done and frontend-feedback from one Git-visible commit
Required feedback bundle:
- docs/pantheon-feedback/KW-03-evidence-refs/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/KW-03-evidence-refs/API_GAP_REQUESTS.json
- docs/pantheon-feedback/KW-03-evidence-refs/UI_DECISIONS.md
- docs/pantheon-feedback/KW-03-evidence-refs/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/KW-03-evidence-refs-ui-done.yaml` using `.coordination/requests/KW-03-evidence-refs-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/KW-03-evidence-refs-frontend-feedback.yaml` using `.coordination/requests/KW-03-evidence-refs-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/pantheon-handoffs/KW-03-evidence-refs/FRONTEND_CHANGE_SPEC.md
- docs/bff/KW-03-evidence-refs.md
- docs/pantheon-handoffs/KW-03-evidence-refs
- docs/examples/KW-03-evidence-refs.json
