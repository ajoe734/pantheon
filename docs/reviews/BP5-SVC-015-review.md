# Review: BP5-SVC-015 — Remove BFF snapshot and default fallback from the normal integration path

Reviewer: Claude
Date: 2026-04-16
Outcome: **APPROVED**

---

## Acceptance Criteria Check

### 1. Operator and persona BFF reads no longer treat snapshot/default seed mode as the normal integration path

**PASS.**

- `ReadSurfaceStore.__init__` defaults `allow_local_snapshot_fallback=False` when the caller passes `None` or omits it.
- When fallback is disabled, `_load_or_seed()` sets `self._data = {}` instead of seeding from `_default_read_data()`.
- `_local_fallback()` returns `None` for every dataset when the flag is `False`, so no read surface silently consumes local seed data on the mainline path.
- Tests explicitly pass `allow_local_snapshot_fallback=True` to access the seeded fixture data — this is the correct pattern.

### 2. Degraded operator behavior is explicit and backend-owned instead of UI-invented

**PASS.**

- Missing backend data surfaces as `None` / `[]` returns from the public API — callers receive an honest "not available" signal rather than fabricated defaults.
- `dataset_source()` reports `"missing"` when no backend data and local fallback is disabled.
- `command_executor.py`: `_configured_base_url()` raises `RuntimeError` when neither `PANTHEON_INTERNAL_API_URL` nor `PANTHEON_GOVERNANCE_API_URL` is set. `execute_command_with_status()` catches this and returns `CommandStatus.FAILED` with code `COMMAND_BACKEND_UNCONFIGURED`. Commands never pretend to succeed when unconfigured.

---

## L1 Policy Alignment (BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md §5.1)

Policy requirement:
> BFF 正常整合路徑不得以本地 seed、snapshot、或隱性 localhost backend 預設假裝 backend 已就緒

Implementation: satisfied. Mainline `ReadSurfaceStore` instances start with empty data and only read from canonical/service backends.

Policy requirement:
> command-submission path 也必須指向明確配置的 backend API；不得以環境別名或隱性 fallback 假裝 governance/control backend 已可用

Implementation: satisfied. `command_executor.py` raises `RuntimeError` and returns `COMMAND_BACKEND_UNCONFIGURED` when the env vars are absent.

---

## Test Suite Verification

All test suites pass with exit code 0:
- `test_command_executor.py` — 11 tests OK
- `test_read_store_deployment.py` — seeded + canonical overlay tests PASS
- `test_persona_management.py` — PASS
- `test_read_store_incident.py` — PASS
- `test_consultation_surfaces.py` — PASS
- `test_w3_surfaces.py` — PASS
- `test_w4_remaining_catalog.py` — PASS
- `smoke_test.py` — PASS

---

## Minor Observations (non-blocking)

1. `get_kill_switch_status()` is entirely local-fallback-dependent. When the flag is disabled, it returns a conservative default `{"active": False, "status": "armed"}`. This is safe per §5.2 of the HA policy (kill switch must not depend on BFF uptime), and the policy explicitly allows the secondary control path to stay outside BFF.

2. `rollbacks`, `freeze_orders`, `all_rollbacks` have no service-backed adapter path. Without local fallback, they return empty. This is honest behavior — better empty than invented.

Neither observation requires changes in this task's scope.

---

## Decision

**Approved.** Implementation correctly removes snapshot/default fallback from the normal integration path and surfaces backend unavailability honestly. All acceptance criteria met, all tests pass, L1 policy alignment confirmed.
