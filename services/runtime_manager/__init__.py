"""Canonical Runtime Manager package.

Exports the pure runtime mutation state machine (RuntimeManagerService),
RuntimeBinding models/store, and KillSwitchController primitives.
"""
from __future__ import annotations

from services.runtime_manager.kill_switch_controller import (
    FAST_PATH_BENCHMARK_ITERATIONS,
    FAST_PATH_DISPATCH_CHANNEL,
    FAST_PATH_LATENCY_TARGET_MS,
    EmergencyClass,
    EmergencyTrigger,
    HardTriggerReason,
    KillSwitchActionType,
    KillSwitchAuditEntry,
    KillSwitchCommand,
    KillSwitchController,
    KillSwitchError,
    KillSwitchOutcome,
    SafeModeState,
    SoftTriggerReason,
)
from services.runtime_manager.runtime_binding import (
    DeploymentMode,
    RollbackActionType,
    RuntimeBinding,
    RuntimeBindingError,
    RuntimeBindingStatus,
    RuntimeBindingStore,
    utc_now,
    validate_binding,
)
from services.runtime_manager.service import (
    DeployPlanRequest,
    EvolutionFreezeRequest,
    EvolutionRedeployRequest,
    EvolutionRetrainRequest,
    KillSwitchRequest,
    ReplaceRuntimeRequest,
    RollbackRequest,
    RuntimeManagerError,
    RuntimeManagerService,
)

__all__ = [
    # Service
    "RuntimeManagerService",
    "RuntimeManagerError",
    "DeployPlanRequest",
    "ReplaceRuntimeRequest",
    "RollbackRequest",
    "KillSwitchRequest",
    "EvolutionFreezeRequest",
    "EvolutionRetrainRequest",
    "EvolutionRedeployRequest",
    # RuntimeBinding
    "RuntimeBinding",
    "RuntimeBindingError",
    "RuntimeBindingStatus",
    "RuntimeBindingStore",
    "DeploymentMode",
    "RollbackActionType",
    "validate_binding",
    "utc_now",
    # KillSwitch
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
