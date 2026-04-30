"""Experiment backend configuration.

EXPERIMENT_BACKEND defaults to "mlflow".

"wandb" is selectable only for the offline local run store. It remains guarded
by an explicit feature flag plus offline-only mode, does not import the W&B SDK,
and does not activate online sync.

See services/registry/experiments/WANDB_ACTIVATION.md for the full gate.
"""

import os

EXPERIMENT_BACKEND: str = (os.getenv("EXPERIMENT_BACKEND", "mlflow").strip().lower() or "mlflow")
WANDB_DEFERRED_PREP_FLAG = "PANTHEON_ENABLE_WANDB_DEFERRED_PREP"
WANDB_OFFLINE_STORE_FLAG = "PANTHEON_ENABLE_WANDB_OFFLINE_STORE"
WANDB_ONLINE_SYNC_FLAG = "PANTHEON_WANDB_ONLINE_SYNC_ENABLED"
WANDB_MODE_ENV = "PANTHEON_WANDB_MODE"

_SUPPORTED_BACKENDS = ("mlflow", "wandb")
_SUPPORTED_WANDB_MODES = ("offline", "dryrun")


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def is_wandb_offline_store_enabled() -> bool:
    return _is_truthy(os.getenv(WANDB_OFFLINE_STORE_FLAG)) or _is_truthy(os.getenv(WANDB_DEFERRED_PREP_FLAG))


def is_wandb_deferred_prep_enabled() -> bool:
    return is_wandb_offline_store_enabled()


def is_wandb_online_sync_enabled() -> bool:
    return _is_truthy(os.getenv(WANDB_ONLINE_SYNC_FLAG))


def selected_wandb_mode(default: str = "offline") -> str:
    mode = os.getenv(WANDB_MODE_ENV, default).strip().lower() or default
    if mode not in _SUPPORTED_WANDB_MODES:
        raise EnvironmentError(
            f"{WANDB_MODE_ENV}={mode!r} is not supported. "
            f"Supported modes: {_SUPPORTED_WANDB_MODES}. "
            "W&B local store stays offline-only."
        )
    return mode


def selected_backend(default: str = "mlflow") -> str:
    backend = os.getenv("EXPERIMENT_BACKEND", default).strip().lower() or default
    if backend not in _SUPPORTED_BACKENDS:
        raise EnvironmentError(
            f"EXPERIMENT_BACKEND={backend!r} is not supported. "
            f"Supported backends: {_SUPPORTED_BACKENDS}."
        )
    if backend == "wandb" and not is_wandb_offline_store_enabled():
        raise EnvironmentError(
            "EXPERIMENT_BACKEND='wandb' is reserved for the offline local run store. "
            f"Set {WANDB_OFFLINE_STORE_FLAG}=1 to opt in. "
            f"{WANDB_DEFERRED_PREP_FLAG}=1 is accepted only as a compatibility alias."
        )
    if backend == "wandb":
        if is_wandb_online_sync_enabled():
            raise EnvironmentError(
                f"{WANDB_ONLINE_SYNC_FLAG}=1 cannot be combined with EXPERIMENT_BACKEND='wandb' "
                "in this offline adapter. Online sync requires a separate approved SDK-backed gate."
            )
        selected_wandb_mode()
    return backend


selected_backend(EXPERIMENT_BACKEND)
