# Review: APP-002-W2-CONTROL-INCIDENT — Harden Incident Control-Path Execution

**Author**: Qwen  
**Reviewer**: Codex  
**Date**: 2026-04-11  
**Status**: Ready for review  

---

## Summary

This task hardens the incident control-path execution for pause, rollback, and kill-switch operations, ensuring they execute through authoritative protected paths rather than stub receipts.

---

## Acceptance Criteria Verification

### 1. pause_rollback_killswitch_authoritative ✅

**Pause Runtime** (`internal_api.py:/api/internal/v1/runtimes/<binding_id>/pause`):
- Routes through `RuntimeBindingStore.transition_status()` enforcing the canonical state machine
- Transitions: `active → pending_pause → paused` (pause), `paused → active` (resume)
- Idempotent handling for repeated requests
- Full audit record persisted to command state store

**Execute Rollback** (`internal_api.py:/api/internal/v1/rollbacks/execute`):
- Implements the rollback action matrix from `rollback_action_matrix.md`:
  - `replace`: hot-swap artifact, retire old binding
  - `pause_then_replace`: drain orders → pause → swap
  - `liquidate_then_replace`: flatten positions → swap
- Routes through `RuntimeBindingStore` with proper state transitions
- Audit trail includes `rollback_id`, `status_before/after`, `position_lineage_updated`

**Activate Kill-Switch** (`internal_api.py:/api/internal/v1/kill-switch`):
- Routes through `KillSwitchController.dispatch()` — the real fast-path execution engine
- Produces immutable `KillSwitchCommand` → `KillSwitchAuditEntry` → `KillSwitchOutcome`
- Safe-mode state machine advanced atomically per-pool
- Action selection matrix (§7) maps triggers to PAUSE/LIQUIDATE/RISK_OFF/REPLACE/TERMINATE
- `KillSwitchError` handling with full error context

**BFF Command Execution** (`command_executor.py`):
- Dispatch table maps all 6 command types to internal API endpoints
- `_post_json` propagates auth tokens and MFA tokens downstream
- `execute_command_with_status` never raises — returns `(status, result, error)` tuple
- Handles URLError (connection/timeout), HTTPError, TimeoutError, generic Exception

**BFF Background Worker** (`main.py:_process_command`):
- Extracts auth/MFA tokens from submission audit context
- Marks command as PROCESSING before execution
- Enriches audit with execution timeline, executor identity, failure reasons
- Persists both result and enriched audit data

### 2. incident_actions_audited ✅

**Kill-Switch Audit Trail**:
- `KillSwitchAuditEntry` is immutable (frozen dataclass) with `audit_id`, `command_id`, `trigger_id`, `safe_mode_before/after`
- Every dispatch appends to `_audit_log` list — in-memory accumulation for session
- Audit entry includes `outcome_note` with human-readable summary
- `audit_log()` returns a copy — callers cannot mutate history

**Command State Store**:
- JSON file persistence at `/tmp/pantheon/internal_api/commands.json`
- Each record: `command_id`, `type`, `target`, `status`, `submitted_at`, `result`, `error`
- Rollback records include: `rollback_id`, `rollback_action_type`, `position_lineage_updated`, `audit_id`
- Kill-switch records include: `kill_switch_order_id`, `action`, `emergency_class`, `safe_mode_after`, `audit_id`

**RuntimeBinding Transitions**:
- `retired_at` timestamp set automatically on terminal transitions
- Status transitions enforced by `_ALLOWED_STATUS_TRANSITIONS` table
- Invalid transitions raise `RuntimeBindingError` with context

**BFF Audit Context**:
- Command submission records: `operator_id`, `roles_at_submission`, `mfa_verified`, `preconditions_checked`
- Execution enrichment: `execution_completed_at`, `executor`, `downstream_verified`, `failure_reason`, `failure_suggestion`

### 3. degraded_control_guidance_present ✅

**DEGRADED_OPERATOR_PATH.md**:
- Documents partial degradation, total BFF outage, cascading failure scenarios
- Per-surface degradation behavior defined for all 33 L1 canonical surfaces
- Secondary control path specification (CLI + internal API bypass)
- Incident data marked as safety-critical: "never show 'no incidents' when service unreachable"
- Kill-switch status must come from runtime-manager directly; if unavailable, show "status unknown" with last-check timestamp

**BFF Staleness Warnings**:
- `_check_read_surface_state()` returns `StalenessWarning` when `BFF_READ_SURFACE_STATE` ≠ "fresh"
- Command submission includes `staleness_warning` in receipt
- Operators warned to "verify target state via secondary control path before confirming action"

**BFF_SURFACE_INVENTORY.md**:
- Incident surfaces (IN-01 to IN-05) documented with degraded behavior
- Kill-switch (IN-05): "if unavailable, show 'status unknown' with last-check timestamp"

---

## Test Results

| Test Suite | Tests | Status |
|---|---|---|
| `test_kill_switch_controller.py` | 8/8 | ✅ PASS |
| `test_internal_api_incident.py` | 11/11 | ✅ PASS |
| `test_read_store_incident.py` | 14/14 | ✅ PASS |
| **Total** | **33/33** | **✅ PASS** |

### Test Coverage Highlights
- Hard trigger dispatches → PAUSE command, priority 1, fast_path channel
- Soft trigger dispatches → RISK_OFF mode, priority 2
- REPLACE action requires fallback_artifact_id + version
- Unknown trigger reasons rejected with `KillSwitchError`
- Manual safe-mode advance enforces transition table
- Pause: active → pending_pause → paused
- Resume: paused → active
- Rollback with replace: retires old binding
- Full kill-switch path: controller → audit trail → command persistence
- Incident listing with status/severity/pool filters
- Postmortem detail with root_cause + action_items
- Kill-switch status shape validation
- Composed view helpers (evolution decisions, rollbacks, telemetry)

---

## Architecture Notes

### Kill-Switch Controller Design
- Hot path (`dispatch()`) is pure Python with no blocking I/O — deterministic latency benchmarking
- `FAST_PATH_DISPATCH_CHANNEL = "runtime_manager_fast_path"` — commands bypass normal review queue
- Safe-mode state machine: NORMAL → GUARDED → RISK_OFF → PAUSED → RECOVERY_TESTING → NORMAL_RESTORED
- Hard triggers always escalate to at least GUARDED from NORMAL

### Rollback Action Matrix
- `replace`: preserve & inherit — new binding takes over existing book
- `pause_then_replace`: drain & inherit — stabilize before transfer
- `liquidate_then_replace`: flatten — all exposure removed before new binding
- Position lineage: `opened_by_artifact_id` immutable, `current_managed_by_binding_id` updated on cutover

### BFF Command Pipeline
1. Submit → precondition validation (roles + params) → concurrent modification check → degraded mode check
2. Persist with audit context → queue background task → return 202 receipt
3. Background worker: PROCESSING → execute via internal API → enrich audit → persist result

---

## Files Modified/Verified

| File | Role |
|---|---|
| `services/control-plane/bff/main.py` | BFF endpoints, command submission, background worker |
| `services/control-plane/bff/command_executor.py` | Internal API dispatch with auth propagation |
| `services/control-plane/bff/read_store.py` | Incident read surfaces with seed data |
| `services/control_plane/internal_api.py` | Authoritative pause/rollback/kill-switch execution |
| `services/execution/runtime-manager/kill_switch_controller.py` | Fast-path controller, audit, safe-mode |
| `services/execution/runtime-manager/runtime_binding.py` | Canonical state machine, store, transitions |
| `services/execution/runtime-manager/rollback_action_matrix.md` | Rollback action mapping |
| `services/execution/runtime-manager/authority_matrix.md` | Write authority boundaries |
| `services/control-plane/bff/DEGRADED_OPERATOR_PATH.md` | Degraded control guidance |
