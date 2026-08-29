"""Execution plane package.

Re-exports canonical runtime control symbols from services.runtime_manager.
"""
from __future__ import annotations

from services.runtime_manager import (
    FAST_PATH_BENCHMARK_ITERATIONS,
    FAST_PATH_DISPATCH_CHANNEL,
    FAST_PATH_LATENCY_TARGET_MS,
    DeploymentMode,
    EmergencyClass,
    EmergencyTrigger,
    HardTriggerReason,
    KillSwitchActionType,
    KillSwitchAuditEntry,
    KillSwitchCommand,
    KillSwitchController,
    KillSwitchError,
    KillSwitchOutcome,
    RollbackActionType,
    RuntimeBinding,
    RuntimeBindingError,
    RuntimeBindingStatus,
    RuntimeBindingStore,
    SafeModeState,
    SoftTriggerReason,
    utc_now,
    validate_binding,
)

__all__ = [
    "RuntimeBinding",
    "RuntimeBindingError",
    "RuntimeBindingStatus",
    "RuntimeBindingStore",
    "DeploymentMode",
    "RollbackActionType",
    "validate_binding",
    "utc_now",
    "KillSwitchController",
    "KillSwitchError",
    "EmergencyTrigger",
    "KillSwitchCommand",
    "KillSwitchAuditEntry",
    "KillSwitchOutcome",
    "KillSwitchActionType",
    "SafeModeState",
    "SoftTriggerReason",
    "HardTriggerReason",
    "EmergencyClass",
    "FAST_PATH_DISPATCH_CHANNEL",
    "FAST_PATH_LATENCY_TARGET_MS",
    "FAST_PATH_BENCHMARK_ITERATIONS",
]
