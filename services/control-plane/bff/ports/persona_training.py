"""Persona Training, Replay, and Rapid-Evaluation narrow domain ports.

Re-exports typed domain ports, protocols, and factory functions for Persona
Registry profiles, Training Session trainer/replay, and Rapid-Evaluation.
"""
from __future__ import annotations

try:
    from domain_ports.persona_training import (
        PersonaRegistryReadsPort,
        TrainingSessionTrainerPort,
        RapidEvaluationPort,
        RapidEvaluationOwnership,
        PersonaTrainingDomainPort,
    )
except ImportError:
    from services.control_plane.bff.domain_ports.persona_training import (  # type: ignore[no-redef]
        PersonaRegistryReadsPort,
        TrainingSessionTrainerPort,
        RapidEvaluationPort,
        RapidEvaluationOwnership,
        PersonaTrainingDomainPort,
    )

__all__ = [
    "PersonaRegistryReadsPort",
    "TrainingSessionTrainerPort",
    "RapidEvaluationPort",
    "RapidEvaluationOwnership",
    "PersonaTrainingDomainPort",
]
