"""Governed TRL preference-learning adapter for Pantheon.

Governance boundary:
- Input: governed FB-002 preference events (approve/edit/reject) with metadata
- Output: registry-ready model_artifact (artifact_state=draft) + registry_entry
- TRL or its dependencies never write directly to registry, runtime, or LEAN.
- CI / smoke tests use StubDPOBackend (no TRL install required).
"""
from .trl_adapter import (
    GovernedPreferencePairAdapter,
    PreferencePair,
    PreferencePairDataset,
    StubDPOBackend,
    TRLDPOBackend,
    TrainingConfig,
    TRLRunResult,
    TRLWorkflowError,
    run_trl_dpo_workflow,
)

__all__ = [
    "GovernedPreferencePairAdapter",
    "PreferencePair",
    "PreferencePairDataset",
    "StubDPOBackend",
    "TRLDPOBackend",
    "TrainingConfig",
    "TRLRunResult",
    "TRLWorkflowError",
    "run_trl_dpo_workflow",
]
