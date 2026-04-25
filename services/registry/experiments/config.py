"""Experiment backend configuration.

EXPERIMENT_BACKEND selects which tracking backend is active.
Currently only "mlflow" is implemented. "wandb" is reserved for future activation
once the following gate conditions are met:
  - MLflow has ≥30 days operational history
  - An explicit operator preference is documented
  - RegistryExperimentAdapter is generalized to accept non-MLflow backends
  - Canonical artifact_state / deployment_stage migration is landed in the experiment bridge

See services/registry/experiments/WANDB_ACTIVATION.md for the full activation gate.
See services/learning/DEFERRED_OSS_ACTIVATION_MAP.md §5 for the consolidated status.
"""

import os

EXPERIMENT_BACKEND: str = os.getenv("EXPERIMENT_BACKEND", "mlflow")

_SUPPORTED_BACKENDS = ("mlflow",)
# "wandb" is intentionally not in _SUPPORTED_BACKENDS until the activation gate is passed.

if EXPERIMENT_BACKEND not in _SUPPORTED_BACKENDS:
    raise EnvironmentError(
        f"EXPERIMENT_BACKEND={EXPERIMENT_BACKEND!r} is not yet supported. "
        f"Supported backends: {_SUPPORTED_BACKENDS}. "
        "W&B activation requires completing the gate in WANDB_ACTIVATION.md."
    )
