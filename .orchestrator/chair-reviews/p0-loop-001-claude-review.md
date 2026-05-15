# Review: P0-LOOP-001 — Add minimum paper operating loop smoke

Reviewer: Claude
Date: 2026-05-01
Status: **APPROVED**

## Verification

```
pytest -q services/control-plane/bff/test_p0_paper_operating_loop_smoke.py \
  services/telemetry/test_runtime_summary_projection.py \
  services/telemetry/test_paper_runtime_ingest_contract.py \
  services/execution/lean_runtime/test_bootstrap_contract.py \
  services/execution/lean_runtime/test_runtime_context.py
# Result: 29 passed in 13.70s
```

## Acceptance Criteria Check

| Criterion | Status | Evidence |
|---|---|---|
| smoke uses pantheon/lean, not lean-platform | ✅ PASS | `PANTHEON_LEAN_SOURCE_PATH = "pantheon/lean"` in bootstrap_contract.py; test explicitly asserts `lean-platform` absent from all emitted event metadata |
| no live broker action or preview mock is used | ✅ PASS | `bootstrap_request.runtime_config.live_broker_enabled == False`; `runtime_summary["health_summary"]["broker"] == "not_applicable"`; all events carry `execution_mode == "paper"` and `deployment_stage == "paper"` |

## Loop Coverage

The smoke test covers the full minimum operating loop defined in SA-11 §12.2:

- **DeploymentPlan** — created via `StagePlanner.create_plan(target_stage="paper")`
- **RuntimeBinding** — materialised via `RuntimeManagerService.deploy()`
- **RuntimeBootstrapRequest / PantheonRuntimeContext** — produced by `materialize_runtime_bootstrap_request`; stage-guarded (`expected_stage="paper"`)
- **Paper heartbeat** — emitted by `PaperRuntimeService.drain_once()` → `_maybe_emit_heartbeat()`
- **Telemetry projection** — `TelemetryIngestService` + `RuntimeSummaryProjectionStore` receive and project the heartbeat event
- **BFF runtime-state** — `GET /api/v1/operator/runtime-state` returns the projected summary with correct `runtime_binding_id`, `deployment_stage`, `last_heartbeat_at`, `engine_bridge_repo`, and `health_summary.broker`

## Implementation Quality Notes

- Bracket order implementation in `executor.py` is cleanly staged: paper/sim guard passes, live stages are blocked. `logged_only` vs `submitted_to_broker` semantics are distinct and auditable.
- `RuntimeBindingResolver` falls back to `runtime_context` when the runtime manager cannot be reached — appropriate for isolated tests.
- `RuntimeTelemetryEmitter.build_event` validates that `deployment_stage == "paper"` before emitting; all required binding fields are checked.
- `_LoopbackTelemetryEmitter` adapter in the smoke test properly routes telemetry through the real `TelemetryIngestService` rather than mocking it out.

## Result

All acceptance criteria passed. Implementation is correct and well-scoped. Approved for finalization.
