"""Agora strategy-workshop sub-module — capability: agora.workshop.v1.

Re-exports the persistence store so callers can do:
  from agora.strategy_workshop import MemoryWorkshopStore, make_workshop_store
"""
from .store import (  # noqa: F401
    MemoryWorkshopStore,
    PostgresWorkshopStore,
    make_workshop_store,
    BACKEND_ENV,
    DSN_ENV,
    SCHEMA_ENV,
    DEFAULT_SCHEMA,
)

__all__ = [
    "MemoryWorkshopStore",
    "PostgresWorkshopStore",
    "make_workshop_store",
    "BACKEND_ENV",
    "DSN_ENV",
    "SCHEMA_ENV",
    "DEFAULT_SCHEMA",
    "StrategyReconstructionResult",
    "reconstruct_strategy_from_events",
    "run_reconstruction_worker",
]

from .reconstruction import (  # noqa: F401
    StrategyReconstructionResult,
    reconstruct_strategy_from_events,
)
def __getattr__(name: str):
    if name == "run_reconstruction_worker":
        from .runner import run_reconstruction_worker

        return run_reconstruction_worker
    raise AttributeError(name)


VERSION = "1.0"
