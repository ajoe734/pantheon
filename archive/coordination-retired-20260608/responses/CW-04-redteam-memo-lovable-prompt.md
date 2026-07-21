Build the `CW-04-redteam-memo` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/CW-04-redteam-memo-bff-gap.yaml` using `.coordination/requests/CW-04-redteam-memo-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `redteam-memo`.
Workbench: `consultation-workbench`.
Allowed endpoints:
- GET /api/v1/consult/memos
- GET /api/v1/consult/memos/{memo_id}
Published Pantheon dependencies:
- .coordination/responses/CW-04-redteam-memo-contract-ready.yaml
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- fetch memo list from GET /api/v1/consult/memos with the published status, page_token, and page_size semantics only
- fetch memo detail from GET /api/v1/consult/memos/{memo_id} only
- navigate to detail using route_href from list rows; do not construct memo URLs from ids
- render the mapping panel from session_to_memo_mapping exactly as supplied
- resolve evidence navigation only from evidence_refs[].link; do not construct links from id or artifact_ref
- render freshness from meta.staleness and surface health from meta.surfaces.redteam_memo.state without conflating them
- show the governance CTA only when allowedActions.canInitiateGovernanceReview is true
- keep recommendations[] as plain ordered strings; do not add severity or workflow columns
- emit a bff-gap handoff if any required field is absent
Required feedback bundle:
- docs/pantheon-feedback/CW-04-redteam-memo/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/CW-04-redteam-memo/API_GAP_REQUESTS.json
- docs/pantheon-feedback/CW-04-redteam-memo/UI_DECISIONS.md
- docs/pantheon-feedback/CW-04-redteam-memo/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/CW-04-redteam-memo-ui-done.yaml` using `.coordination/requests/CW-04-redteam-memo-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/CW-04-redteam-memo-frontend-feedback.yaml` using `.coordination/requests/CW-04-redteam-memo-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/screens/CW-04-redteam-memo.md
- docs/pantheon-handoffs/CW-04-redteam-memo/FRONTEND_CHANGE_SPEC.md
- docs/bff/CW-04-redteam-memo.md
- docs/pantheon-handoffs/CW-04-redteam-memo
- docs/examples/CW-04-redteam-memo.json
