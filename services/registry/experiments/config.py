"""Experiment backend configuration.

EXPERIMENT_BACKEND defaults to "mlflow".

"wandb" is now selectable only for the 2026-04-25 deferred-prep exception and
remains guarded by an explicit feature flag plus offline-only mode. It does not
change the default backend and does not satisfy the W&B activation gate.

See services/registry/experiments/WANDB_ACTIVATION.md for the full gate.
"""

import os

EXPERIMENT_BACKEND: str = (os.getenv("EXPERIMENT_BACKEND", "mlflow").strip().lower() or "mlflow")
WANDB_DEFERRED_PREP_FLAG = "PANTHEON_ENABLE_WANDB_DEFERRED_PREP"
WANDB_MODE_ENV = "PANTHEON_WANDB_MODE"

_SUPPORTED_BACKENDS = ("mlflow", "wandb")
_SUPPORTED_WANDB_MODES = ("offline", "dryrun")


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def is_wandb_deferred_prep_enabled() -> bool:
    return _is_truthy(os.getenv(WANDB_DEFERRED_PREP_FLAG))


def selected_wandb_mode(default: str = "offline") -> str:
    mode = os.getenv(WANDB_MODE_ENV, default).strip().lower() or default
    if mode not in _SUPPORTED_WANDB_MODES:
        raise EnvironmentError(
            f"{WANDB_MODE_ENV}={mode!r} is not supported. "
            f"Supported modes: {_SUPPORTED_WANDB_MODES}. "
            "W&B deferred prep stays offline-only."
        )
    return mode


def selected_backend(default: str = "mlflow") -> str:
    backend = os.getenv("EXPERIMENT_BACKEND", default).strip().lower() or default
    if backend not in _SUPPORTED_BACKENDS:
        raise EnvironmentError(
            f"EXPERIMENT_BACKEND={backend!r} is not supported. "
            f"Supported backends: {_SUPPORTED_BACKENDS}."
        )
    if backend == "wandb" and not is_wandb_deferred_prep_enabled():
        raise EnvironmentError(
            "EXPERIMENT_BACKEND='wandb' is reserved for deferred prep only. "
            f"Set {WANDB_DEFERRED_PREP_FLAG}=1 to opt into the offline scaffold."
        )
    if backend == "wandb":
        selected_wandb_mode()
    return backend


selected_backend(EXPERIMENT_BACKEND)
