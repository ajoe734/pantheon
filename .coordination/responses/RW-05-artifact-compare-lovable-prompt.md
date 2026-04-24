Build the `RW-05-artifact-compare` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
Pantheon has resolved the returned list-authority gap: the live
`GET /api/v1/artifacts` route now returns
`artifacts[].allowedActions.canCompare`, and the registry plus compare-page
selectors must treat that backend-owned list field as the sole compare
selection authority.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/RW-05-artifact-compare-bff-gap.yaml` using `.coordination/requests/RW-05-artifact-compare-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `artifact-compare`.
Workbench: `research-workbench`.
Screen ID: `screen-artifact-compare`.
Allowed endpoints:
- GET /api/v1/artifacts
- GET /api/v1/artifacts/{artifact_id}
- GET /api/v1/artifacts/compare
Published Pantheon dependencies:
- .coordination/responses/RW-05-artifact-compare-contract-ready.yaml
- .coordination/responses/RW-05-artifact-compare-backend-delivery.yaml
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- build or refresh the Artifact Registry, Artifact Detail, and Artifact Compare surfaces in front-ai-trading-system against the resolved list compare-authority contract
- use only the existing BFF client
- do not add raw fetch calls in component files
- render artifact list, detail, and compare data from Pantheon BFF only
- do not derive compare output, version ancestry, or provenance pairs client-side
- gate compare affordances from backend-shaped allowedActions only
- emit a bff-gap handoff if any required field is absent
- publish ui-done and frontend-feedback from one Git-visible commit after syncing the refreshed packet
Required feedback bundle:
- docs/pantheon-feedback/RW-05-artifact-compare/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/RW-05-artifact-compare/API_GAP_REQUESTS.json
- docs/pantheon-feedback/RW-05-artifact-compare/UI_DECISIONS.md
- docs/pantheon-feedback/RW-05-artifact-compare/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/RW-05-artifact-compare-ui-done.yaml` using `.coordination/requests/RW-05-artifact-compare-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/RW-05-artifact-compare-frontend-feedback.yaml` using `.coordination/requests/RW-05-artifact-compare-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/pantheon-handoffs/RW-05-artifact-compare/FRONTEND_CHANGE_SPEC.md
- docs/bff/RW-05-artifact-compare.md
- docs/pantheon-handoffs/RW-05-artifact-compare
- docs/examples/RW-05-artifact-compare.json
