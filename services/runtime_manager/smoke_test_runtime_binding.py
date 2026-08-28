"""
Smoke tests for RuntimeBinding platform object.

Covers:
  - RuntimeBinding creation with required cross-object references
  - persona_capital_binding_id is mandatory
  - plan_id is mandatory
  - deployment_mode must be a valid stage
  - single-runtime enforcement
  - status state machine transitions
  - terminal-state immutability
  - rollback field validation
  - persistence round-trip

Run with: python3 services/execution/runtime-manager/smoke_test_runtime_binding.py
"""
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from services.runtime_manager.runtime_binding import (
    DeploymentMode,
    RollbackActionType,
    RuntimeBinding,
    RuntimeBindingError,
    RuntimeBindingStatus,
    RuntimeBindingStore,
    validate_binding,
)

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"


def check(label: str, condition: bool) -> bool:
    status = PASS if condition else FAIL
    print(f"  [{status}] {label}")
    return condition


def _base_binding(**overrides) -> RuntimeBinding:
    kwargs = dict(
        binding_id="aaaaaaaa-0000-0000-0000-000000000001",
        runtime_id="runtime-lean-001",
        capital_pool_id="pool-alpha",
        artifact_id="artifact-strat-001",
        artifact_version="1.0.0",
        deployment_mode=DeploymentMode.PAPER.value,
        effective_at="2026-04-10T10:00:00Z",
        status=RuntimeBindingStatus.ACTIVE.value,
        plan_id="plan-001",
        persona_capital_binding_id="pcb-001",
    )
    kwargs.update(overrides)
    return RuntimeBinding(**kwargs)


def test_valid_creation():
    print("\n[1] Valid creation with all required fields")
    ok = True

    b = _base_binding()
    ok &= check("binding_id is set", b.binding_id == "aaaaaaaa-0000-0000-0000-000000000001")
    ok &= check("plan_id is set", b.plan_id == "plan-001")
    ok &= check("persona_capital_binding_id is set", b.persona_capital_binding_id == "pcb-001")
    ok &= check("deployment_mode is paper", b.deployment_mode == "paper")
    ok &= check("status is active", b.status == "active")
    ok &= check("is not terminal", not b.is_terminal())

    errors = validate_binding(b)
    ok &= check("no validation errors", errors == [])

    return ok


def test_missing_plan_id():
    print("\n[2] Missing plan_id raises error")
    ok = True
    try:
        b = _base_binding(plan_id="")
        errors = validate_binding(b)
        ok &= check("validation catches missing plan_id", any("plan_id" in e for e in errors))
    except Exception as exc:
        ok &= check(f"exception raised (acceptable): {exc}", True)
    return ok


def test_missing_persona_capital_binding_id():
    print("\n[3] Missing persona_capital_binding_id raises error")
    ok = True
    try:
        b = _base_binding(persona_capital_binding_id="")
        errors = validate_binding(b)
        ok &= check(
            "validation catches missing persona_capital_binding_id",
            any("persona_capital_binding_id" in e for e in errors),
        )
    except Exception as exc:
        ok &= check(f"exception raised (acceptable): {exc}", True)
    return ok


def test_invalid_deployment_mode():
    print("\n[4] Invalid deployment_mode raises RuntimeBindingError")
    ok = True
    raised = False
    try:
        _base_binding(deployment_mode="staging")
    except RuntimeBindingError:
        raised = True
    ok &= check("RuntimeBindingError raised for invalid mode", raised)
    return ok


def test_all_valid_modes():
    print("\n[5] All valid deployment_mode values accepted")
    ok = True
    for mode in DeploymentMode:
        try:
            b = _base_binding(deployment_mode=mode.value)
            ok &= check(f"mode={mode.value} accepted", b.deployment_mode == mode.value)
        except RuntimeBindingError as exc:
            ok &= check(f"mode={mode.value} should be valid: {exc}", False)
    return ok


def test_single_runtime_enforcement():
    print("\n[6] Single-runtime rule prevents duplicate active bindings")
    ok = True
    store = RuntimeBindingStore()

    b1 = _base_binding(binding_id="aaaaaaaa-0000-0000-0000-000000000001")
    store.create(b1, single_runtime_enforced=True)

    b2 = _base_binding(binding_id="aaaaaaaa-0000-0000-0000-000000000002")
    raised = False
    try:
        store.create(b2, single_runtime_enforced=True)
    except RuntimeBindingError:
        raised = True
    ok &= check("second active binding for same pool is rejected", raised)

    # After retiring b1, b2 should be allowed
    store.retire(b1.binding_id)
    try:
        store.create(b2, single_runtime_enforced=True)
        ok &= check("new binding allowed after retiring previous", True)
    except RuntimeBindingError as exc:
        ok &= check(f"new binding should succeed after retire: {exc}", False)

    return ok


def test_status_transitions():
    print("\n[7] Status machine transitions")
    ok = True
    store = RuntimeBindingStore()

    b = _base_binding(binding_id="aaaaaaaa-0000-0000-0000-000000000010")
    store.create(b, single_runtime_enforced=False)

    # active -> pending_pause
    updated = store.transition_status(b.binding_id, "pending_pause")
    ok &= check("active -> pending_pause", updated.status == "pending_pause")

    # pending_pause -> paused
    updated = store.transition_status(b.binding_id, "paused")
    ok &= check("pending_pause -> paused", updated.status == "paused")

    # paused -> retired (terminal)
    updated = store.retire(b.binding_id)
    ok &= check("paused -> retired", updated.status == "retired")
    ok &= check("retired_at is set", updated.retired_at is not None)
    ok &= check("is_terminal() is True", updated.is_terminal())

    # Terminal: no further transitions
    raised = False
    try:
        store.transition_status(b.binding_id, "active")
    except RuntimeBindingError:
        raised = True
    ok &= check("terminal binding rejects further transitions", raised)

    return ok


def test_rollback_fields():
    print("\n[8] Rollback fields — action type must be set when parent is set")
    ok = True

    b_with_rollback = _base_binding(
        binding_id="aaaaaaaa-0000-0000-0000-000000000020",
        rollback_parent="aaaaaaaa-0000-0000-0000-000000000001",
        rollback_action_type=RollbackActionType.REPLACE.value,
    )
    errors = validate_binding(b_with_rollback)
    ok &= check("valid rollback binding has no errors", errors == [])

    # rollback_parent without rollback_action_type
    b_bad = _base_binding(
        binding_id="aaaaaaaa-0000-0000-0000-000000000021",
        rollback_parent="aaaaaaaa-0000-0000-0000-000000000001",
        rollback_action_type=None,
    )
    errors = validate_binding(b_bad)
    ok &= check("missing rollback_action_type detected", any("rollback_action_type" in e for e in errors))

    # invalid rollback_action_type
    raised = False
    try:
        _base_binding(rollback_action_type="magic_rollback")
    except RuntimeBindingError:
        raised = True
    ok &= check("invalid rollback_action_type raises error", raised)

    return ok


def test_persistence_round_trip():
    print("\n[9] Persistence round-trip")
    ok = True

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        path = Path(tf.name)

    store1 = RuntimeBindingStore(path=path)
    b = _base_binding(binding_id="aaaaaaaa-0000-0000-0000-000000000030")
    store1.create(b, single_runtime_enforced=False)

    store2 = RuntimeBindingStore(path=path)
    loaded = store2.get(b.binding_id)
    ok &= check("loaded binding_id matches", loaded is not None and loaded.binding_id == b.binding_id)
    ok &= check("loaded plan_id matches", loaded is not None and loaded.plan_id == b.plan_id)
    ok &= check(
        "loaded persona_capital_binding_id matches",
        loaded is not None and loaded.persona_capital_binding_id == b.persona_capital_binding_id,
    )
    ok &= check(
        "loaded deployment_mode matches",
        loaded is not None and loaded.deployment_mode == b.deployment_mode,
    )

    path.unlink(missing_ok=True)
    return ok


def test_terminal_state_requires_retired_at():
    print("\n[10] Terminal state requires retired_at")
    ok = True

    # retired without retired_at => validation error
    b = _base_binding(status=RuntimeBindingStatus.RETIRED.value, retired_at=None)
    errors = validate_binding(b)
    ok &= check("retired without retired_at triggers validation error", any("retired_at" in e for e in errors))

    # failed without retired_at => validation error
    b2 = _base_binding(status=RuntimeBindingStatus.FAILED.value, retired_at=None)
    errors2 = validate_binding(b2)
    ok &= check("failed without retired_at triggers validation error", any("retired_at" in e for e in errors2))

    return ok


def main():
    tests = [
        test_valid_creation,
        test_missing_plan_id,
        test_missing_persona_capital_binding_id,
        test_invalid_deployment_mode,
        test_all_valid_modes,
        test_single_runtime_enforcement,
        test_status_transitions,
        test_rollback_fields,
        test_persistence_round_trip,
        test_terminal_state_requires_retired_at,
    ]

    results = []
    for t in tests:
        try:
            results.append(t())
        except Exception as exc:
            print(f"  [EXCEPTION] {exc}")
            results.append(False)

    passed = sum(results)
    total = len(results)
    print(f"\n{'='*50}")
    print(f"Results: {passed}/{total} test groups passed")
    if passed == total:
        print("ALL PASS")
        sys.exit(0)
    else:
        print("SOME FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
