"""Governed FinRL deferred-prep adapter for Pantheon.

This package provides a repo-local single-agent RL prep lane only. It does not
reopen the RL approval gate or authorize production training.
"""

from .finrl_adapter import (
    DeferredPrepGate,
    FinRLDeferredPrepError,
    FinRLPPOBackend,
    GovernedFinRLPolicyAdapter,
    PolicyTrainingConfig,
    StubFinRLBackend,
    run_finrl_workflow,
)

__all__ = [
    "DeferredPrepGate",
    "FinRLDeferredPrepError",
    "FinRLPPOBackend",
    "GovernedFinRLPolicyAdapter",
    "PolicyTrainingConfig",
    "StubFinRLBackend",
    "run_finrl_workflow",
]
