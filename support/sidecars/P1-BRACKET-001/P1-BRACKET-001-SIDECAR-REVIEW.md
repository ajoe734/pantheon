# P1-BRACKET-001 Sidecar Review Packet

Task ID: `P1-BRACKET-001-SIDECAR-REVIEW`
Parent task: `P1-BRACKET-001` — Guarded paper/sim bracket order execution
Helper kind: `review_packet`
Owner: `Claude`
Reviewer: `Codex2`
Scope: support artifact only; this packet does not change L1 canonical truth, core contracts, runtime code, registry code, or governance implementation.

## Purpose

This packet consolidates the review evidence for `P1-BRACKET-001` so the parent owner (`Codex2`) has a single reference when finalizing the parent task. The formal reviewer approval is at `.orchestrator/chair-reviews/p1-bracket-001-claude-review.md` (status: **APPROVED**).

---

## Implementation Summary

`P1-BRACKET-001` added a two-layer bracket execution guard to `services/execution/lean_runtime/executor.py` and wired the `PaperExecutionAlgorithm` in `paper_runtime.py` to support guarded simulated child-order submission.

### Key constants (single source of truth)

```python
BRACKET_ORDER_STATUS_LOGGED_ONLY = "logged_only"
BRACKET_ORDER_STATUS_SUBMITTED_TO_BROKER = "submitted_to_broker"
_BRACKET_EXECUTION_STAGES = {"paper", "sim", "simulation"}
_BRACKET_ENTRY_COMBINATIONS = {("BUY", "LONG"), ("SELL", "SHORT")}
```

### Guard logic (`_bracket_execution_guard`)

Layer 1 — runtime stage: `_runtime_stage(algo)` must return a value in `_BRACKET_EXECUTION_STAGES`. Any other value (including `"live"`, `"canary"`, or empty string) → `allowed: False`.

Layer 2 — explicit flag: `BracketOrderExecutionEnabled` / `bracket_order_execution_enabled` must be truthy → `allowed: False` when disabled.

Guard failure always routes to `_record_bracket_order_logged(..., submitted_to_broker=False, broker_submission_status="logged_only")`.

### Execution paths

| Condition | Result |
|---|---|
| Guard blocked (wrong stage or flag=off) | `logged_only`, `submitted_to_broker=False` |
| Non-entry action/direction | `logged_only`, `submitted_to_broker=False` |
| Invalid entry quantity or price | `logged_only`, `submitted_to_broker=False` |
| Submission method missing | `logged_only`, `submitted_to_broker=False` (no partial submission) |
| Paper/sim guard passes, entry signal | `submitted_to_broker`, `submitted_to_broker=True`; child legs stored in `_open_bracket_orders` |

### Child-leg price calculation

Long entry (`BUY/LONG`): stop = `entry_price × (1 − stop_loss_pct)`, take-profit = `entry_price × (1 + take_profit_pct)`, exit quantity = `-abs(entry_quantity)`.

Short entry (`SELL/SHORT`): stop = `entry_price × (1 + stop_loss_pct)`, take-profit = `entry_price × (1 − take_profit_pct)`, exit quantity = `+abs(entry_quantity)`.

### Paper runtime wiring

`PaperExecutionAlgorithm` exposes:
- `SubmitBracketOrder(...)` — stores simulated child legs and returns a `bracket_order_id`.
- `RecordBracketOrderLogged(...)` — emits an `OrderEvent` with `event_type="bracket_order_logged"`, distinguishing `"bracket_submitted_to_broker"` vs `"bracket_logged_only"` actions.

`PaperRuntimeService.snapshot()` surfaces `bracket_order_execution_enabled`, `bracket_order_execution_stage`, and `open_bracket_orders` for operator observability.

Environment variable `PANTHEON_BRACKET_ORDER_EXECUTION_ENABLED=false` disables the flag at runtime initialization.

---

## Acceptance Criteria Verification

### 1. Paper/sim bracket order path guarded ✅

Two-layer guard (`_bracket_execution_guard`) enforces stage ∈ `{"paper","sim","simulation"}` AND explicit enable flag. Any stage outside this set (including `"live"`) returns `allowed: False` unconditionally, independent of the flag.

Evidence: `test_live_bracket_order_remains_logged_only_even_if_guard_flag_is_set` — `DeploymentStage="live"` with `BracketOrderExecutionEnabled=True` → `logged_only`, no child orders.

### 2. `logged_only` and `submitted_to_broker` semantics remain distinct ✅

- Named constants used everywhere; no string literals in decision logic.
- `_record_bracket_order_logged()` called on every bracket path (guard fail, non-entry, invalid legs, submission error, success) with correct status + boolean.
- `PaperExecutionAlgorithm.RecordBracketOrderLogged()` propagates both fields into `OrderEvent` and through the telemetry pipeline.
- Telemetry metrics use distinct action names: `"bracket_submitted_to_broker"` vs `"bracket_logged_only"`.
- Snapshot exposes `bracket_order_execution_enabled` and `bracket_order_execution_stage`.

Evidence: `test_guarded_paper_bracket_order_submits_simulated_children` verifies `submitted_to_broker=True` and correct child legs. `test_bracket_order_is_logged_only_not_broker_submitted` verifies `submitted_to_broker=False` path.

### 3. Live broker submission remains fail-closed without activation guard ✅

Stage check in `_bracket_execution_guard` rejects `"live"` at Layer 1 regardless of the enable flag value. No live broker SDK call, no production activation path added.

Evidence: `test_live_bracket_order_remains_logged_only_even_if_guard_flag_is_set`; `test_runtime_telemetry_emitter_rejects_non_paper_stage` (telemetry emitter also rejects non-paper `deployment_stage`).

---

## Test Evidence

Command: `python3 -m pytest services/execution/lean_runtime/test_executor.py services/execution/lean_runtime/test_paper_runtime.py`
Result: **13 tests passed**, 0 failures, 0 errors.

### test_executor.py (7 tests)

| Test | Guard path | Expected status |
|---|---|---|
| `test_bracket_order_is_logged_only_not_broker_submitted` | No stage set | `logged_only` / `submitted_to_broker=False` |
| `test_guarded_paper_bracket_order_submits_simulated_children` | paper+enabled, SubmitBracketOrder | `submitted_to_broker` / `submitted_to_broker=True` |
| `test_guarded_paper_bracket_order_can_submit_with_lean_order_methods` | paper+enabled, fallback StopMarketOrder/LimitOrder | `submitted_to_broker` / `submitted_to_broker=True` |
| `test_paper_bracket_order_guard_disabled_remains_logged_only` | paper+flag=False | `logged_only` / `submitted_to_broker=False` |
| `test_missing_child_order_method_logs_only_without_partial_submission` | paper+enabled, missing LimitOrder | `logged_only` / `submitted_to_broker=False`, no partial |
| `test_live_bracket_order_remains_logged_only_even_if_guard_flag_is_set` | live+enabled (fail-closed) | `logged_only` / `submitted_to_broker=False` |
| `test_guarded_sim_short_bracket_order_submits_inverse_child_legs` | sim+enabled, SELL/SHORT | `submitted_to_broker=True`, inverse legs |

### test_paper_runtime.py (6 tests)

| Test | Focus |
|---|---|
| `test_drain_once_executes_signal_and_updates_runtime_state` | End-to-end paper fill and telemetry |
| `test_snapshot_without_drain_reports_truthful_ready_state` | Zero-drain readiness state |
| `test_guarded_paper_bracket_order_event_is_submitted_to_paper_broker` | Integration: guard→SubmitBracketOrder→event→telemetry |
| `test_bracket_order_guard_disabled_remains_logged_only` | `PANTHEON_BRACKET_ORDER_EXECUTION_ENABLED=false` env path |
| `test_runtime_telemetry_emitter_builds_canonical_paper_heartbeat` | Telemetry envelope schema |
| `test_runtime_telemetry_emitter_rejects_non_paper_stage` | Telemetry emitter live-stage rejection |

---

## SA-20 Risk Register Update

`R-EXE-003` in `docs/04/pantheon_sa/SA-20_v2_risk_register_corrected.md` documents this risk and its acceptance:

> paper/sim bracket_order_logged distinguishes logged_only from submitted_to_broker;
> guarded paper/sim submissions record submitted_to_broker=true and open simulated child legs;
> live broker submission remains fail-closed without activation guard.

All three acceptance conditions are satisfied by the P1-BRACKET-001 implementation.

---

## Files Reviewed

- `services/execution/lean_runtime/executor.py` — guard logic, bracket execution, logged/submitted semantics
- `services/execution/lean_runtime/paper_runtime.py` — PaperExecutionAlgorithm, PaperRuntimeService, telemetry emitter
- `services/execution/lean_runtime/test_executor.py` — 7 unit tests
- `services/execution/lean_runtime/test_paper_runtime.py` — 6 integration tests
- `docs/04/pantheon_sa/SA-20_v2_risk_register_corrected.md` — R-EXE-003 acceptance criteria

---

## Parent Task Dependency Boundary

`P1-BRACKET-001` depends on `P0-LIVE-GUARD-001` (done). The sidecar does not weaken that floor:

- Live stage fail-closed behavior is independent of the bracket execution flag.
- Bracket parameters on non-paper/sim signals remain audit evidence only.
- `bracket_order_logged` telemetry semantics are not changed.
- No production broker readiness claim is introduced.

---

## Reviewer Notes For Codex2

This is a support-only packet. It does not:
- Approve any L1 canonical truth change.
- Claim production broker readiness.
- Supersede the formal review at `.orchestrator/chair-reviews/p1-bracket-001-claude-review.md`.

For parent finalization, Codex2 should:

1. Confirm `P1-BRACKET-001` status is `review_approved` in `ai-status.json`.
2. Verify the 13 tests still pass: `python3 -m pytest services/execution/lean_runtime/test_executor.py services/execution/lean_runtime/test_paper_runtime.py`.
3. Stage only files that belong to `P1-BRACKET-001` for the task-scoped commit.
4. Run `AI_NAME=Codex2 ./scripts/ai-status.sh done P1-BRACKET-001 "<checkpoint>"` after the commit.

---

## Handoff

Ready for sidecar review by `Codex2`.

Reviewer focus:
- Confirm this packet is support-only and does not promote canonical truth.
- Confirm the evidence summary is accurate against the actual implementation files.
- Confirm the 13-test count and acceptance criteria mapping are correct.

Parent finalization decision remains with `Codex2` as the owner of `P1-BRACKET-001`.
