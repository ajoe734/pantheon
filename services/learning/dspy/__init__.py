"""LP-001 DSPy persona optimization adapter."""

from .adapter import (
    ALLOWED_ACTOR_ROLES,
    ALLOWED_PROMOTION_STATES,
    DSPY_VERSION_PIN,
    GovernedPreferenceAdapter,
    PreparedExample,
    PreparedDataset,
    StubBootstrapFewShotBackend,
    TrainingConfig,
    DSPyWorkflowError,
    evaluate_predictions,
    run_dspy_workflow,
)

__all__ = [
    "ALLOWED_ACTOR_ROLES",
    "ALLOWED_PROMOTION_STATES",
    "DSPY_VERSION_PIN",
    "GovernedPreferenceAdapter",
    "PreparedExample",
    "PreparedDataset",
    "StubBootstrapFewShotBackend",
    "TrainingConfig",
    "DSPyWorkflowError",
    "evaluate_predictions",
    "run_dspy_workflow",
]
