Build the `RW-03-analyze` UI flow in `front-ai-trading-system` using only Pantheon APIs.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/RW-03-analyze-bff-gap.yaml` using `.coordination/requests/RW-03-analyze-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `research-analyze`.
Workbench: `research-workbench`.
Screen ID: `screen-research-analyze`.
Allowed endpoints:
- GET /api/v1/research/analysis
- GET /api/v1/research/analysis/{analysis_id}
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- do not group metrics by prefix, substring, or naming convention
- do not compute side-by-side diffs from multiple analysis payloads
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- fetch analysis list from GET /api/v1/research/analysis only
- send ticket_id, experiment_id, status, date_range, page_token, and page_size exactly as published
- render metric_groups[] in backend-supplied order without client-side grouping or reordering
- render comparative_summary from the single detail payload; do not fetch additional payloads for comparison
- navigate drilldowns through links.workbench_detail, links.linked_ticket_detail, and links.linked_experiment_detail only
- render degradation state from meta.surfaces.analysis_results using the PKT-005 substrate
- emit a bff-gap handoff if any required field is absent
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/RW-03-analyze-ui-done.yaml` using `.coordination/requests/RW-03-analyze-ui-done.example.yaml` as the template. Sync that file back to GitHub and stop so Pantheon supervisor can pick up review/integration work automatically.
References:
- docs/screens/RW-03-analyze.md
- docs/pantheon-handoffs/RW-03-analyze/FRONTEND_CHANGE_SPEC.md
- docs/bff/RW-03-analyze.md
- docs/pantheon-handoffs/RW-03-analyze
- docs/examples/RW-03-analyze.json
