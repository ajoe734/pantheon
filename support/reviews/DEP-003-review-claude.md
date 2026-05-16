# DEP-003 Review

Reviewer: Claude
Owner: Codex
Date: 2026-05-16

## Verdict: Approved

## Scope Verified

- `GET /api/deployment/projections` — list projections with strategy_id, capital_pool_id, target_stage, status filters
- `GET /api/deployment/projections/{plan_id}` — single-plan projection by plan_id
- `GET /api/deployment/plans/{plan_id}/projection` — alias delegating to the above
- `DeploymentProjectionReadModelService._build_projection()` — derived-only composition; no write calls to any store
- `DeploymentProjectionReadModelResponse` model with `projection_contract="DEP-003"`, `derived_only=True`, full source_status, lifecycle_state, summary

## Review Findings

**Derived-only boundary: correct.** `DeploymentProjectionReadModelService` has no calls to any store `.put()` / `.bootstrap_for_plan()` / `.record_*`. It reads plan, saga, approval, registry, and runtime binding state and composes them without write authority.

**source_status semantics: correct.** Keys `deployment_plan`, `approval_decision`, `runtime_binding`, `deployment_saga`, `registry_entry`, `execution_projection` are populated with `"canonical"`, `"missing"`, `"invalid"`, `"derived"`, or `"invalid_source"` depending on what is resolvable. This makes read-model health observable without side effects.

**lifecycle_state derivation: correct.** Terminal plan statuses (`rejected`, `aborted`, `failed`) → `"terminal"`; `runtime_status == "active"` or plan executed → `"active"`; saga present → `"saga:<saga_status>"`; approved plan → `"ready_for_dispatch"`.

**actual_stage vs projected_stage: correct.** `projected_stage = target_stage`; `actual_stage` comes from runtime binding deployment_mode/deployment_stage when present, falling back to target_stage if executed, else current_stage.

**RuntimeBinding lookup: correct.** Reads from `PANTHEON_RUNTIME_BINDING_STORE_PATH` → `PANTHEON_RUNTIME_DATA_DIR/runtime_bindings.json` → `/tmp/pantheon/runtime-manager/bindings.json` with graceful missing-file handling.

**Test coverage: complete.** 18 tests pass:
- `test_projection_read_model_exposes_derived_plan_view` — verifies all projection fields for a plan with approval and registry but no runtime binding
- `test_projection_read_model_joins_runtime_and_saga_state` — verifies runtime_binding_id, runtime_id, runtime_status, actual_stage, deployment_saga_id, saga_status, and lifecycle_state when runtime binding is present post-dispatch

**contract.md and README.md: updated.** DEP-003 routes documented under Service Boundary, Routes, and Storage sections.

## No Required Changes

Implementation is correct and complete. Returning to Codex for closeout.
