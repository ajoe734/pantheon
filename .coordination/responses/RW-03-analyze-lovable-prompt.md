Build the `RW-03-analyze` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/RW-03-analyze-bff-gap.yaml` using `.coordination/requests/RW-03-analyze-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `research-analyze`.
Workbench: `research-workbench`.
Screen ID: `screen-research-analyze`.
Allowed endpoints:
- GET /api/v1/research/analysis
- GET /api/v1/research/analysis/{analysis_id}
Published Pantheon dependencies:
- .coordination/responses/RW-03-analyze-contract-ready.yaml
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- fetch analysis list from GET /api/v1/research/analysis only
- send ticket_id, experiment_id, status, date_range, page_token, and page_size exactly as published
- render metric_groups[] in backend-supplied order without client-side grouping or reordering
- render comparative_summary from the single detail payload; do not fetch additional payloads for comparison
- navigate drilldowns through links.workbench_detail, links.linked_ticket_detail, and links.linked_experiment_detail only
- render degradation state from meta.surfaces.analysis_results using the PKT-005 substrate
- emit a bff-gap handoff if any required field is absent
Required feedback bundle:
- docs/pantheon-feedback/RW-03-analyze/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/RW-03-analyze/API_GAP_REQUESTS.json
- docs/pantheon-feedback/RW-03-analyze/UI_DECISIONS.md
- docs/pantheon-feedback/RW-03-analyze/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/RW-03-analyze-ui-done.yaml` using `.coordination/requests/RW-03-analyze-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/RW-03-analyze-frontend-feedback.yaml` using `.coordination/requests/RW-03-analyze-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/screens/RW-03-analyze.md
- docs/pantheon-handoffs/RW-03-analyze/FRONTEND_CHANGE_SPEC.md
- docs/bff/RW-03-analyze.md
- docs/pantheon-handoffs/RW-03-analyze
- docs/examples/RW-03-analyze.json
