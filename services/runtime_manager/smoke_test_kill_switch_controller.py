#!/usr/bin/env python3
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from services.runtime_manager.kill_switch_controller import (
    EmergencyTrigger,
    FAST_PATH_BENCHMARK_ITERATIONS,
    FAST_PATH_DISPATCH_CHANNEL,
    FAST_PATH_LATENCY_TARGET_MS,
    HardTriggerReason,
    KillSwitchActionType,
    KillSwitchController,
    SafeModeState,
    SoftTriggerReason,
)

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"


def check(label: str, condition: bool) -> bool:
    status = PASS if condition else FAIL
    print(f"  [{status}] {label}")
    return condition


def _trigger(reason: str, **overrides) -> EmergencyTrigger:
    payload = {
        "reason": reason,
        "capital_pool_id": "pool-smoke",
        "actor_id": "smoke-runner",
        "binding_id": "binding-smoke-001",
    }
    payload.update(overrides)
    return EmergencyTrigger(**payload)


def test_hard_path_routes_to_runtime_manager_fast_path() -> bool:
    print("\n[1] Hard path routes through runtime-manager fast path")
    ok = True
    controller = KillSwitchController()
    outcome = controller.dispatch(
        _trigger(HardTriggerReason.OPERATOR_EMERGENCY_STOP.value),
        issued_at="2026-04-11T05:10:00Z",
    )

    ok &= check(
        "dispatch_path is runtime_manager_fast_path",
        outcome.command.dispatch_path == FAST_PATH_DISPATCH_CHANNEL,
    )
    ok &= check(
        "normal review queue is bypassed",
        outcome.command.bypass_review_queue is True,
    )
    ok &= check(
        "hard trigger maps to pause command",
        outcome.command.action_type == KillSwitchActionType.PAUSE.value,
    )
    ok &= check(
        "safe mode advances to paused",
        outcome.safe_mode_after == SafeModeState.PAUSED,
    )
    return ok


def test_audit_trail_is_preserved() -> bool:
    print("\n[2] Audit trail is preserved for dispatch and recovery transitions")
    ok = True
    controller = KillSwitchController()
    trigger = _trigger(HardTriggerReason.UNAUTHORIZED_ARTIFACT_BINDING_MISMATCH.value)
    outcome = controller.dispatch(trigger, issued_at="2026-04-11T05:11:00Z")
    controller.advance_safe_mode(
        trigger.capital_pool_id,
        SafeModeState.RECOVERY_TESTING,
        actor_id="incident-owner",
        note="Fallback artifact verified in paper validation",
    )

    audit_entries = controller.audit_log()
    ok &= check("two audit entries recorded", len(audit_entries) == 2)
    ok &= check(
        "dispatch audit entry references command_id",
        audit_entries[0].command_id == outcome.command.command_id,
    )
    ok &= check(
        "manual safe-mode advance is also audited",
        audit_entries[1].reason == "manual_safe_mode_advance",
    )
    ok &= check(
        "manual safe-mode advance has a unique command id",
        audit_entries[0].command_id != audit_entries[1].command_id,
    )
    return ok


def test_replace_path_requires_fallback_identity() -> bool:
    print("\n[3] Replace path carries fallback artifact identity")
    ok = True
    controller = KillSwitchController()
    outcome = controller.dispatch(
        _trigger(SoftTriggerReason.LOADER_ANOMALY_NO_BREACH.value),
        action_override=KillSwitchActionType.REPLACE,
        fallback_artifact_id="artifact-safe-002",
        fallback_artifact_version="2.0.1",
        issued_at="2026-04-11T05:12:00Z",
    )

    ok &= check(
        "replace action selected",
        outcome.command.action_type == KillSwitchActionType.REPLACE.value,
    )
    ok &= check(
        "fallback artifact id recorded",
        outcome.command.fallback_artifact_id == "artifact-safe-002",
    )
    ok &= check(
        "fallback artifact version recorded",
        outcome.command.fallback_artifact_version == "2.0.1",
    )
    ok &= check(
        "safe mode enters guarded after replace",
        outcome.safe_mode_after == SafeModeState.GUARDED,
    )
    return ok


def test_dispatch_latency_target() -> bool:
    print("\n[4] Dispatch latency benchmark")
    ok = True
    controller = KillSwitchController()
    durations_ms: list[float] = []

    for index in range(FAST_PATH_BENCHMARK_ITERATIONS):
        trigger = _trigger(
            SoftTriggerReason.DRIFT_ABOVE_WARNING_THRESHOLD.value,
            capital_pool_id=f"pool-bench-{index % 8}",
            binding_id=f"binding-bench-{index % 8}",
        )
        start = time.perf_counter_ns()
        controller.dispatch(trigger, issued_at="2026-04-11T05:13:00Z")
        end = time.perf_counter_ns()
        durations_ms.append((end - start) / 1_000_000)

    durations_ms.sort()
    mean_ms = sum(durations_ms) / len(durations_ms)
    p95_index = max(0, math.ceil(len(durations_ms) * 0.95) - 1)
    p95_ms = durations_ms[p95_index]
    max_ms = durations_ms[-1]

    print(
        "    "
        f"iterations={FAST_PATH_BENCHMARK_ITERATIONS} "
        f"mean={mean_ms:.4f}ms p95={p95_ms:.4f}ms max={max_ms:.4f}ms "
        f"target_p95<={FAST_PATH_LATENCY_TARGET_MS:.2f}ms"
    )
    ok &= check("p95 latency meets target", p95_ms <= FAST_PATH_LATENCY_TARGET_MS)
    return ok


def main() -> None:
    tests = [
        test_hard_path_routes_to_runtime_manager_fast_path,
        test_audit_trail_is_preserved,
        test_replace_path_requires_fallback_identity,
        test_dispatch_latency_target,
    ]

    results = []
    for test in tests:
        try:
            results.append(test())
        except Exception as exc:
            print(f"  [EXCEPTION] {exc}")
            results.append(False)

    passed = sum(results)
    total = len(results)
    print(f"\n{'=' * 50}")
    print(f"Results: {passed}/{total} test groups passed")
    if passed == total:
        print("ALL PASS")
        sys.exit(0)
    print("SOME FAILED")
    sys.exit(1)


if __name__ == "__main__":
    main()
