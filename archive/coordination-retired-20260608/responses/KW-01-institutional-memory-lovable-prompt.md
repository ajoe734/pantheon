Build the `KW-01-institutional-memory` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/KW-01-institutional-memory-bff-gap.yaml` using `.coordination/requests/KW-01-institutional-memory-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `institutional-memory`.
Workbench: `knowledge-workbench`.
Allowed endpoints:
- GET /api/v1/knowledge/memory
- GET /api/v1/knowledge/memory/{entry_id}
Published Pantheon dependencies:
- .coordination/responses/KW-01-institutional-memory-contract-ready.yaml
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- fetch memory list from GET /api/v1/knowledge/memory with filter query params only
- fetch memory detail from GET /api/v1/knowledge/memory/{entry_id} only
- use route_href from BFF list rows for navigation; do not construct URLs from raw entry_id
- use source_event.href from BFF detail for source links; do not construct URLs from type and id
- show non-dismissable PKT-005 degradation banner when meta.surfaces.memory_list is degraded or unavailable
- show non-dismissable PKT-005 degradation banner when meta.surfaces.entry_detail is degraded or unavailable
- show degradation indicator on source event panel when meta.surfaces.source_context is degraded or unavailable
- display superseded entries with a visual indicator; do not hide them
- do not filter, sort, rank, or invent filter vocab client-side
- do not expose any create, archive, supersede, or update actions
Required feedback bundle:
- docs/pantheon-feedback/KW-01-institutional-memory/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/KW-01-institutional-memory/API_GAP_REQUESTS.json
- docs/pantheon-feedback/KW-01-institutional-memory/UI_DECISIONS.md
- docs/pantheon-feedback/KW-01-institutional-memory/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/KW-01-institutional-memory-ui-done.yaml` using `.coordination/requests/KW-01-institutional-memory-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/KW-01-institutional-memory-frontend-feedback.yaml` using `.coordination/requests/KW-01-institutional-memory-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/screens/KW-01-institutional-memory.md
- docs/pantheon-handoffs/KW-01-institutional-memory/FRONTEND_CHANGE_SPEC.md
- docs/bff/KW-01-institutional-memory.md
- docs/pantheon-handoffs/KW-01-institutional-memory
- docs/examples/KW-01-institutional-memory.json
