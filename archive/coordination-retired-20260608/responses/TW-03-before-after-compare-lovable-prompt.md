Build the `TW-03-before-after-compare` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/TW-03-before-after-compare-bff-gap.yaml` using `.coordination/requests/TW-03-before-after-compare-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `before-after-compare`.
Workbench: `trainer-workbench`.
Screen ID: `screen-before-after-compare`.
Allowed endpoints:
- GET /api/v1/trainer/sessions/{session_id}/preview
- POST /api/v1/trainer/sessions/{session_id}/preview
Published Pantheon dependencies:
- .coordination/responses/TW-03-before-after-compare-contract-ready.yaml
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- render compare header, summary, metric deltas, warning hierarchy, and control diff from GET /api/v1/trainer/sessions/{session_id}/preview
- call POST /api/v1/trainer/sessions/{session_id}/preview only for manual refresh with refresh_mode = manual
- poll only GET /api/v1/trainer/sessions/{session_id}/preview?eval_id={eval_id} while status = pending and polling.enabled = true
- stop polling when status resolves, deadline_at passes, or meta.surfaces.trainer_preview becomes degraded or unavailable
- surface refresh CTA only when allowedActions.canRefreshPreview is true
- render preview_unavailable as explicit degraded compare copy, not as loading
- preserve backend ordering for warnings[] and backend-provided warning_count_by_level summary
- emit a bff-gap handoff if any required field is absent
Required feedback bundle:
- docs/pantheon-feedback/TW-03-before-after-compare/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/TW-03-before-after-compare/API_GAP_REQUESTS.json
- docs/pantheon-feedback/TW-03-before-after-compare/UI_DECISIONS.md
- docs/pantheon-feedback/TW-03-before-after-compare/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/TW-03-before-after-compare-ui-done.yaml` using `.coordination/requests/TW-03-before-after-compare-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/TW-03-before-after-compare-frontend-feedback.yaml` using `.coordination/requests/TW-03-before-after-compare-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/screens/TW-03-before-after-compare.md
- docs/pantheon-handoffs/TW-03-before-after-compare/FRONTEND_CHANGE_SPEC.md
- docs/bff/TW-03-before-after-compare.md
- docs/pantheon-handoffs/TW-03-before-after-compare
- docs/examples/TW-03-before-after-compare.json
