"""Governed FinRL deferred-prep adapter for Pantheon.

This package provides a repo-local single-agent RL prep lane only. It does not
reopen the RL approval gate or authorize production training.
"""

from .finrl_adapter import (
    DeferredPrepGate,
    FinRLDQNBackend,
    FinRLDeferredPrepError,
    FinRLPPOBackend,
    GovernedFinRLPolicyAdapter,
    PolicyTrainingConfig,
    StubFinRLBackend,
    prepared_finrl_dataset_checksum,
    run_finrl_workflow,
)

__all__ = [
    "DeferredPrepGate",
    "FinRLDQNBackend",
    "FinRLDeferredPrepError",
    "FinRLPPOBackend",
    "GovernedFinRLPolicyAdapter",
    "PolicyTrainingConfig",
    "StubFinRLBackend",
    "prepared_finrl_dataset_checksum",
    "run_finrl_workflow",
]
