# Review: BFF-WRITE-P0-WIZARD-006

- **Reviewer:** Claude
- **Reviewed at:** 2026-05-30
- **PR:** [#658](https://github.com/ajoe734/pantheon/pull/658)
- **Decision:** APPROVE

## Scope

Adds `POST /api/v1/deployment-plans` (wizard step 3) to
`services/control-plane/bff/main.py` (P0-6). Creates a deployment plan in
`pending_approval` state from a runtime binding + artifact + capital pool.
Plus matching read_store overlay support for local fallback.

## Pattern conformance

- Auth: `_extract_identity` + `_require_operator_role`.
- Required fields enum check: `binding_id`, `artifact_id`, `capital_pool_id` (each via `_deployment_plan_create_required_string` → 422 on empty).
- `deployment_mode` validated against `{paper, live}` → 422.
- Artifact approved-state gate via `_raise_if_deployment_artifact_not_approved`; documented "permissive on unknown artifact" fallback acceptable for BFF facade where registry may be partial.
- Idempotency: `_resolve_final_idempotency_key` + `_GOV_BFF_IDEMPOTENCY` with request-hash conflict (409 on payload change), replay returns cached result.
- Plan ID conflict guard (409 `RESOURCE_CONFLICT`) for `client_plan_id` collisions.
- Headers: `X-Correlation-Id`, `X-Request-Id`, `X-Dry-Run`, both `Idempotency-Key` aliases.
- Envelope: `{data, meta}` with `evidenceKind="deployment_plan.create"` (both camelCase + snake_case for back-compat), `dryRun`, `correlationId`, `requestId`, `snapshot_at`, `surfaces.deployment_plans`.
- Status codes: 201 Created on happy path; 200 on dry-run override; 409 / 422 on error paths.
- Audit SSE: `deployment-plan.created` event published with persona_id resolved via binding lookup.

## Bug check

None spotted.

- `payload` is `Body(...)` (FastAPI required), so empty body returns 422 automatically.
- Dry-run path returns preview with stable plan_id but skips persistence + audit.
- `read_store.get_deployment_plan` collision check runs only on non-dry-run path (correct).
- `_project_deployment_plan_create_response` handles both `plan_id` / `id` fallbacks.
- Persona ID lookup tolerant of missing binding (returns None, doesn't raise).

## Test coverage

7 tests in `services/control-plane/bff/test_bff_write_gap_2026_05_28.py`:

1. `test_post_deployment_plan_creates_pending_approval_and_replays` — happy + idempotent replay
2. `test_post_deployment_plan_honors_locked_flag`
3. `test_post_deployment_plan_dry_run_returns_200_without_persistence`
4. `test_post_deployment_plan_validates_deployment_mode`
5. `test_post_deployment_plan_requires_binding_id`
6. `test_post_deployment_plan_rejects_unapproved_artifact` — 409
7. `test_post_deployment_plan_idempotency_conflict_on_changed_payload` — 409

## CI / trailer-check failure note

PR originally had 1 fail on "Commit trailers" — was on a merge-from-dev
commit (which is acceptable, no trailers required on merge commits).
The actual code commit `bff05b71` passes trailer check.

## Decision

APPROVE — merge.
