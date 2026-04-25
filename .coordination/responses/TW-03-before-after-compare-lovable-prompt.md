Build the `TW-03-before-after-compare` UI flow in `front-ai-trading-system` using only Pantheon APIs.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/TW-03-before-after-compare-bff-gap.yaml` using `.coordination/requests/TW-03-before-after-compare-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `before-after-compare`.
Workbench: `trainer-workbench`.
Screen ID: `screen-before-after-compare`.
Allowed endpoints:
- GET /api/v1/trainer/sessions/{session_id}/preview
- POST /api/v1/trainer/sessions/{session_id}/preview
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
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/TW-03-before-after-compare-ui-done.yaml` using `.coordination/requests/TW-03-before-after-compare-ui-done.example.yaml` as the template. Sync that file back to GitHub and stop so Pantheon supervisor can pick up review/integration work automatically.
References:
- docs/screens/TW-03-before-after-compare.md
- docs/pantheon-handoffs/TW-03-before-after-compare/FRONTEND_CHANGE_SPEC.md
- docs/bff/TW-03-before-after-compare.md
- docs/pantheon-handoffs/TW-03-before-after-compare
- docs/examples/TW-03-before-after-compare.json
