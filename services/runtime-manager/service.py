"""Bridge re-exporting RuntimeManagerService from canonical services.runtime_manager."""
from __future__ import annotations

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
    "RuntimeManagerService",
    "RuntimeManagerError",
    "DeployPlanRequest",
    "ReplaceRuntimeRequest",
    "RollbackRequest",
    "KillSwitchRequest",
    "EvolutionFreezeRequest",
    "EvolutionRetrainRequest",
    "EvolutionRedeployRequest",
]
