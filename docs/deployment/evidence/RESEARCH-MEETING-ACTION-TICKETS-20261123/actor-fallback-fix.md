# Actor fallback fix (review of 8f1a97b21, P2)

- `_ticket_body_actor` in services/research/main.py again resolves
  `body.actor_id OR patch.actor_id OR (authenticated actor | operator)`.
  Authenticated callers still get 403 for a mismatched outer/nested actor_id.
- Regression: `test_ordinary_unauthenticated_patch_preserves_nested_actor_id`
  (real HTTP + isolated PostgreSQL): PATCH {"patch":{"status":"in_progress","actor_id":"quant-1"}}
  records transitioned_by=quant-1.
- Validation (isolated postgres:16-alpine, QuantLib 1.43, numpy 1.26.4):
  meeting tickets + single-owner routes + orchestrator HTTP + tests/research_write_owner:
  149 passed, 3 failed. Failures are environment-only and not touched by this change:
  2x `vectorbt` has no attribute `Portfolio` (unpinned vectorbt in scratch venv),
  1x test_bff_routes_distinguish_data_empty_unavailable_and_missing (source typed_store vs service_client, env config).
  Reviewer's own pinned env previously reported these suites green; not re-proven here.
- Not claimed: approval, merge, deployed, accepted.
