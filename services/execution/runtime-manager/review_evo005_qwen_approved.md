# EVO-005 Review — Qwen

**Task**: Implement kill-switch and safe-mode fast path
**Owner**: Codex
**Reviewer**: Qwen (auto-reassigned from Gemini after critical error)
**Date**: 2026-04-11
**Status**: review → review_approved

---

## Verdict: APPROVED

All three acceptance criteria are met. The implementation is correct, auditable, and aligned with L1 policy.

---

## Acceptance Criteria Verification

| # | Acceptance Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Emergency actions bypass normal review queues but still flow through runtime-manager | ✅ MET | `KillSwitchCommand.dispatch_path = "runtime_manager_fast_path"`, `bypass_review_queue = True`, but commands are dispatched to Runtime Manager (never directly to LEAN runtime). Invariant #2 in module docstring enforced. |
| 2 | Audit trail is preserved | ✅ MET | Every `dispatch()` creates an immutable `KillSwitchAuditEntry`. `advance_safe_mode()` also emits audit entries. `audit_log()` returns a defensive copy. Entries contain: trigger source, classification, action type, scope, execution result note, timestamps. |
| 3 | Latency target is benchmarked | ✅ MET | Smoke test benchmarks 1000 iterations: mean=0.022ms, p95=0.031ms, max=1.18ms. Target ≤5.0ms. PASS. |

---

## Acceptance Checklist (from EVO-005-SIDECAR-ACCEPTANCE.md)

| # | Item | Status | Notes |
|---|---|---|---|
| A1 | Kill Switch Controller component exists | ✅ PASS | `services/execution/runtime-manager/kill_switch_controller.py` — classifies soft/hard, issues commands to RM |
| A2 | Runtime Manager fast path exists | ✅ PASS | `FAST_PATH_DISPATCH_CHANNEL = "runtime_manager_fast_path"`, distinct from normal evolution review queue |
| A3 | Action types implemented | ✅ PASS | `PAUSE`, `RISK_OFF`, `LIQUIDATE`, `REPLACE`, `TERMINATE` — all defined in `KillSwitchActionType` enum, all selectable via dispatch |
| A4 | Safe mode state machine implemented | ✅ PASS | All 6 states (`NORMAL`, `GUARDED`, `RISK_OFF`, `PAUSED`, `RECOVERY_TESTING`, `NORMAL_RESTORED`) with explicit transition table |
| A5 | Trigger classification logic | ✅ PASS | 6 hard triggers (§6.1) + 5 soft triggers (§6.2) — all enumerated, classification is O(1) and auditable |
| A6 | Action selection matrix implemented | ✅ PASS | `_select_action()` maps every trigger to the correct default action per L1 §7 table |
| A7 | Audit trail preserved | ✅ PASS | `KillSwitchAuditEntry` is frozen dataclass with all required fields. Every dispatch + manual advance creates an entry. |
| A8 | No direct LEAN bypass | ✅ PASS | Dispatch terminates at `KillSwitchCommand` destined for Runtime Manager. No LEAN runtime import or direct call exists. |
| A9 | Latency target validated | ✅ PASS | p95=0.031ms ≤ 5.0ms target. Benchmark in `smoke_test_kill_switch_controller.py` §4. |
| A10 | Secondary control path integration | ⚠️ DEFERRED | Kill switch is callable via the controller API. Admin CLI / protected API integration is tracked in `APP-002-IMPL-CLI` and `APP-002-IMPL-BFF` — downstream tasks, not blockers for this controller contract. |
| A11 | Scope handling correct | ⚠️ PARTIAL | Controller tracks safe-mode per `capital_pool_id`. Persona-scoped and environment-scoped kill switches are not yet modeled — these are downstream integration concerns (APP-002 surfaces), not controller contract gaps. |
| A12 | Integration with EVO-004 normal path | ✅ PASS | Fast path is clearly separated. `EVOLUTION_REVIEW_AND_THRESHOLDS.md` §11.1 rollback row references EVO-005 as the fast-path exception. The `bypass_review_queue=True` flag distinguishes it from normal evolution decisions. |
| A13 | Unit tests | ✅ PASS | 8/8 tests passing. Cover: trigger classification, action selection, state transitions, audit recording, REPLACE validation, invalid transitions, audit log isolation. |
| A14 | Smoke / integration tests | ✅ PASS | 4/4 smoke test groups passing. End-to-end: trigger → controller → command → audit → state update → latency benchmark. |

---

## L1 Policy Alignment

### KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md

| L1 Section | Implementation | Verdict |
|---|---|---|
| §2.2 — Never bypass Runtime Manager | `dispatch_path = "runtime_manager_fast_path"`, no LEAN imports | ✅ Aligned |
| §3.1 — Soft emergency path | 5 soft triggers → `PAUSE`/`RISK_OFF`/`REPLACE` per §7 matrix | ✅ Aligned |
| §3.2 — Hard emergency path | 6 hard triggers → `PAUSE`/`LIQUIDATE`/`RISK_OFF` per §7 matrix | ✅ Aligned |
| §4 — Action types | `pause`, `risk_off`, `liquidate`, `replace`, `terminate` all present | ✅ Aligned |
| §5.1 — Kill Switch Controller | `KillSwitchController` class: classifies, issues commands, records audit | ✅ Aligned |
| §5.2 — Runtime Manager | Commands dispatched to RM fast path; RM is sole binding writer (enforced by caller) | ✅ Aligned |
| §6.1 — Hard triggers (6 items) | All 6 `HardTriggerReason` enum values match L1 exactly | ✅ Aligned |
| §6.2 — Soft triggers (5 items) | All 5 `SoftTriggerReason` enum values match L1 exactly | ✅ Aligned |
| §7 — Action selection matrix | `_select_action()` maps all 11 triggers to correct default actions | ✅ Aligned |
| §9 — Safe mode states (6 states) | All 6 `SafeModeState` values with explicit transition table | ✅ Aligned |
| §10 — v1 decisions (6 items) | All 6 non-negotiable v1 decisions reflected in code invariants | ✅ Aligned |

### EVOLUTION_REVIEW_AND_THRESHOLDS.md

| Section | Alignment | Verdict |
|---|---|---|
| §11.1 — rollback fast-path exception | EVO-005 is the referenced fast-path exception. `bypass_review_queue=True` distinguishes it from normal evolution decisions. | ✅ Aligned |

---

## Code Quality Notes

### Strengths
1. **Immutable dataclasses**: All domain objects (`EmergencyTrigger`, `KillSwitchCommand`, `KillSwitchAuditEntry`, `KillSwitchOutcome`) are frozen — no mutation after creation, ensuring audit integrity.
2. **Pure dispatch path**: `dispatch()` has no blocking I/O. Latency is deterministic and benchmarkable.
3. **Defensive copies**: `audit_log()` returns a new list, preventing caller corruption.
4. **Validation in `__post_init__`**: Trigger reasons, command fields, and safe-mode transitions are validated at construction time, not at dispatch time.
5. **Clear separation of concerns**: `_select_action()` and `_advance_safe_mode()` are pure functions, independently testable.

### Minor Observations (not blockers)
1. **`drawdown_hard_breach` safe mode**: L1 §7 says "liquidate / risk_off" for drawdown hard breach. Implementation maps to `RISK_OFF` action, which advances safe mode to `RISK_OFF` state. This is a valid choice — liquidation is a position-level action that the Runtime Manager executes, while safe mode tracks the operational state. The distinction is correct but worth documenting explicitly in a caller runbook.
2. **Scope granularity**: Current implementation tracks safe-mode per `capital_pool_id`. The L1 policy mentions persona-scoped, pool-scoped, environment-scoped, and all-scoped kill switches. The controller's pool-level tracking is correct for v1; broader scope resolution is a surface-layer concern (APP-002), not a controller gap.
3. **TERMINATE action**: `TERMINATE` is defined in the enum but has no dedicated trigger mapping in `_select_action()`. This is correct — terminate is an escalation of `PAUSE`/`LIQUIDATE` after conditions are met, initiated by the Runtime Manager or operator, not a first-class trigger response.

---

## Test Results

```
Unit tests: 8/8 PASS (python3 -m unittest discover -s services/execution/runtime-manager -p 'test_*.py')
Smoke tests: 4/4 PASS (python3 services/execution/runtime-manager/smoke_test_kill_switch_controller.py)
Latency:    p95=0.031ms ≤ 5.0ms target (1000 iterations)
```

---

## Open Questions (documented in sidecar, resolved for v1)

| Question | Resolution |
|---|---|
| Latency SLA | p95 ≤ 5ms validated. Implementation target is `FAST_PATH_LATENCY_TARGET_MS = 5.0`. |
| Scope precedence | v1 tracks per `capital_pool_id`. Broader scope resolution deferred to APP-002 surfaces. |
| Recovery validation criteria | `recovery_testing → normal_restored` transition is allowed in the table. Specific validation criteria (e.g., N minutes of clean telemetry) are operator-level policy, not controller contract. |
| Dual control | Configurable for v1. Controller does not enforce dual-approval; that is a surface-layer policy concern. |

---

## Conclusion

EVO-005 is **APPROVED**. The KillSwitchController correctly implements the L1 kill-switch and safe-mode fast path. It:

1. Classifies soft vs. hard emergencies per §6
2. Selects default actions per §7 matrix
3. Dispatches commands to the Runtime Manager fast path (never directly to LEAN)
4. Preserves an immutable audit trail for every action
5. Tracks safe-mode state per pool with valid transitions per §9
6. Meets latency targets (p95 ≤ 5ms)
7. Has comprehensive unit and smoke test coverage

The controller contract is ready for downstream integration via APP-002 (admin CLI, protected API, operator console).
