# Review: P1-BRACKET-001 — Guarded paper/sim bracket order execution

- Reviewer: Claude
- Owner: Codex2
- Date: 2026-05-01
- Status: **APPROVED**

## Scope reviewed

- `services/execution/lean_runtime/executor.py`
- `services/execution/lean_runtime/paper_runtime.py`
- `services/execution/lean_runtime/test_executor.py`
- `services/execution/lean_runtime/test_paper_runtime.py`
- `docs/04/pantheon_sa/SA-20_v2_risk_register_corrected.md`

## Acceptance criteria verification

### 1. paper/sim bracket order path guarded ✅

`_bracket_execution_guard()` in `executor.py` enforces a two-layer gate:
- Layer 1: `_runtime_stage(algo)` must return a value in `_BRACKET_EXECUTION_STAGES = {"paper", "sim", "simulation"}`. Any other stage (including `"live"`, `"canary"`, or empty) → `allowed: False`.
- Layer 2: the `BracketOrderExecutionEnabled` / `bracket_order_execution_enabled` flag must be truthy. Disabled flag → `allowed: False`.

Guard failure always produces `logged_only` + `submitted_to_broker=False` via `_record_bracket_order_logged()`.

### 2. logged_only and submitted_to_broker semantics remain distinct ✅

- Named constants `BRACKET_ORDER_STATUS_LOGGED_ONLY = "logged_only"` and `BRACKET_ORDER_STATUS_SUBMITTED_TO_BROKER = "submitted_to_broker"` are the single source of truth throughout the execution path.
- `_record_bracket_order_logged()` always called on every bracket order path (guard failure, non-entry signal, invalid legs, submission error, and successful submission) with the correct status and `submitted_to_broker` boolean.
- `PaperExecutionAlgorithm.RecordBracketOrderLogged()` propagates both fields through the event pipeline into `OrderEvent.broker_submission_status` / `submitted_to_broker`.
- Telemetry metrics distinguish `"bracket_submitted_to_broker"` vs `"bracket_logged_only"` actions.
- Snapshot exposes `bracket_order_execution_enabled` and `bracket_order_execution_stage` for observability.

### 3. live broker submission remains fail-closed without activation guard ✅

`test_live_bracket_order_remains_logged_only_even_if_guard_flag_is_set` demonstrates that `_LiveAlgo` (DeploymentStage="live", BracketOrderExecutionEnabled=True) produces `logged_only` / `submitted_to_broker=False` with guard_stage="live". The stage check in `_bracket_execution_guard()` rejects all non-paper/sim stages unconditionally, independent of the enabled flag.

## Test coverage

13 tests covering all guard paths:
- No-stage algo → logged_only
- paper+enabled → submitted_to_broker via SubmitBracketOrder
- paper+fallback LEAN methods → submitted_to_broker via StopMarketOrder/LimitOrder
- paper+guard disabled → logged_only, no open orders
- Missing LEAN method → logged_only, no partial submission
- live stage (guard flag=True) → logged_only, fail-closed
- sim+short position → submitted_to_broker with inverse legs
- Integration: paper runtime env guard disabled via PANTHEON_BRACKET_ORDER_EXECUTION_ENABLED=false
- Telemetry emitter rejects non-paper deployment_stage

## SA-20 risk register

R-EXE-003 acceptance criteria now correctly documents the implemented boundary:
- `paper/sim bracket_order_logged` distinguishes `logged_only` from `submitted_to_broker`
- guarded paper/sim submissions record `submitted_to_broker=true` and open simulated child legs
- live broker submission remains fail-closed without activation guard

## Verdict

All three acceptance criteria are fully met. No gaps. Approved for finalization.
